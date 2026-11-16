import asyncio
import json

import httpx
import pytest

from app import llm, model_config
from app.models import Profile


def output(patch=None, reply='已记录', search=False):
    return json.dumps({'reply': reply, 'profile_patch': patch or {}, 'search_requested': search}, ensure_ascii=False)


def test_context_bounded_role_order_and_no_demo_or_secret():
    p = Profile(city='郑州', resume_text='客服经验和Excel技能\n' + '其他经历' * 3000)
    snapshot = {'profile': p, 'revision': 3, 'messages': [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': str(i) + 'x' * 5000} for i in range(30)]}
    messages = llm.build_messages(snapshot, '客服工作 secret-key', [
        {'title': '虚构', 'is_demo': True}, {'id': 'real', 'title': '来源记录', 'description': 'test' * 10000, 'source': 'import'}], 'secret-key')
    assert len(messages) <= 19
    assert messages[0]['role'] == messages[1]['role'] == 'system'
    assert messages[-1]['role'] == 'user'
    encoded = json.dumps(messages, ensure_ascii=False)
    assert 'secret-key' not in encoded
    assert '虚构' not in messages[1]['content']
    assert len(encoded) < 65000
    assert 'saved_profile' in encoded
    assert 'relevant_resume_excerpt' in encoded


def test_custom_cycle_patch_and_preference_priority():
    text = '我要求上三休一，最好没有夜班'
    result = llm.parse_model_result(output({
        'accepted_schedules': {'value': [{'kind': 'custom_cycle', 'work_days': 3, 'rest_days': 1}], 'evidence': '上三休一'},
        'accept_night_shift': {'value': False, 'evidence': '最好没有夜班'},
        'priorities': {'value': {'schedule': 'must', 'night_shift': 'prefer'}, 'evidence': '我要求上三休一，最好没有夜班'},
    }), text, Profile())
    assert result['patch']['accepted_schedules'][0]['rest_days'] == 1
    assert result['patch']['priorities']['night_shift'] == 'prefer'


@pytest.mark.parametrize('body', [
    '[]', '{}', '{"reply":"","profile_patch":{}}',
    '{"reply":"OK","profile_patch":[]}',
    '{"reply":"OK","profile_patch":{"city":{"value":"北京","evidence":"不存在"}}}',
    '{"reply":"OK","profile_patch":{"resume_text":{"value":"捏造经历","evidence":"我想找工作"}}}',
    '{"reply":"OK","profile_patch":{},"search_requested":"yes"}',
])
def test_invalid_model_contract_rejected(body):
    with pytest.raises(llm.ModelError):
        llm.parse_model_result(body, '我想找工作', Profile())


def test_cancel_condition_is_explicit_not_historical_notes():
    current = Profile(excluded_terms=['销售'])
    result = llm.parse_model_result(output({'excluded_terms': {'value': [], 'evidence': '取消销售限制'}}),
                                    '请取消销售限制', current)
    assert result['patch']['excluded_terms'] == []


def test_model_timeout_and_bad_content(monkeypatch):
    credentials = {'api_key': 'synthetic-test-key', 'model': 'test', 'base_url': 'https://model.example/v1'}
    original = httpx.AsyncClient

    def timeout(request):
        raise httpx.ReadTimeout('synthetic test timeout', request=request)

    monkeypatch.setattr(llm.httpx, 'AsyncClient', lambda **kwargs: original(**(kwargs | {'transport': httpx.MockTransport(timeout)})))
    with pytest.raises(llm.ModelError) as exc:
        asyncio.run(llm._complete(credentials, [{'role': 'user', 'content': 'test'}]))
    assert exc.value.code == 'model_timeout'
    assert exc.value.status_code == 504
    assert credentials['api_key'] not in str(exc.value)


def test_generation_rejects_unperformed_claims(monkeypatch):
    monkeypatch.setattr(model_config, 'get_credentials', lambda: {'api_key': 'synthetic-test-key', 'model': 'test', 'base_url': 'https://model.example/v1'})

    async def complete(*_args, **_kwargs):
        return output(reply='我已经找到十个岗位了')

    monkeypatch.setattr(llm, '_complete', complete)
    with pytest.raises(llm.ModelError) as exc:
        asyncio.run(llm.generate_reply({'profile': Profile(), 'messages': []}, '帮我找工作', []))
    assert exc.value.code == 'ungrounded_model_claim'
