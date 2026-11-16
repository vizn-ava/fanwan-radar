import pytest
from fastapi.testclient import TestClient

from app import storage, profile_intake, search_tasks, model_config
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('JOB_DEMO_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('JOB_LLM_API_KEY', raising=False)
    def forbidden_search(*args, **kwargs):
        raise AssertionError('Interpreting a paragraph must never start collection')
    monkeypatch.setattr(search_tasks, 'start', forbidden_search)
    with TestClient(app) as test_client:
        yield test_client


def body():
    snapshot = storage.snapshot()
    return {'text': '我想在郑州找客服，月薪至少6000元。', 'draft_patch': {},
            'expected_revision': snapshot['revision'], 'generation': snapshot['generation']}


def preview():
    return {'patch': {'cities': ['郑州'], 'city': '郑州', 'salary_min': 6000},
            'summary': '已整理条件草稿，尚未保存。', 'questions': [], 'evidence': {}, 'changes': []}


def test_preview_is_readonly_and_manual_confirmation_uses_existing_save(client, monkeypatch):
    before = storage.snapshot()
    async def interpret(*args):
        return preview()
    monkeypatch.setattr(profile_intake, 'interpret', interpret)
    response = client.post('/api/profile/interpret', json=body())
    assert response.status_code == 200
    draft = response.json()
    assert draft['mode'] == 'llm_profile_preview'
    assert storage.snapshot() == before
    saved = client.patch('/api/profile', json={'patch': draft['patch'],
                         'expected_revision': draft['revision'], 'generation': draft['generation']})
    assert saved.status_code == 200
    assert saved.json()['profile']['cities'] == ['郑州']
    assert saved.json()['messages'] == []


@pytest.mark.parametrize('operation', ['edit', 'clear'])
def test_changed_or_cleared_profile_invalidates_inflight_preview(client, monkeypatch, operation):
    request = body()
    async def interpret(*args):
        if operation == 'edit':
            storage.update_profile({'city': '上海'})
        else:
            storage.clear()
        return preview()
    monkeypatch.setattr(profile_intake, 'interpret', interpret)
    assert client.post('/api/profile/interpret', json=request).status_code == 409
    current = storage.snapshot()
    assert current['profile'].city == ('上海' if operation == 'edit' else '')
    assert current['messages'] == []


def test_chat_only_revision_does_not_invalidate_unchanged_conditions(client, monkeypatch):
    initial = storage.snapshot()
    async def interpret(*args):
        storage.commit_chat({}, 'question', 'answer', initial['revision'], initial['generation'], 'intake-race')
        return preview()
    monkeypatch.setattr(profile_intake, 'interpret', interpret)
    response = client.post('/api/profile/interpret', json=body())
    assert response.status_code == 200
    assert response.json()['revision'] == initial['revision'] + 1
    assert storage.profile() == initial['profile']


def test_missing_key_and_bad_request_do_not_save_anything(client, monkeypatch):
    before = storage.snapshot()
    async def interpret(*args):
        raise model_config.SettingsError('需要先配置模型', 428, 'model_not_configured')
    monkeypatch.setattr(profile_intake, 'interpret', interpret)
    assert client.post('/api/profile/interpret', json=body()).status_code == 428
    assert client.post('/api/profile/interpret', json=body() | {'unexpected': True}).status_code == 422
    assert storage.snapshot() == before


def test_preview_summary_does_not_miscount_hidden_city_aliases(client, monkeypatch):
    async def interpret(*args):
        result = preview()
        result['summary'] = '已解析9项条件'
        result['changes'] = [{'field': 'cities', 'before': [], 'after': ['郑州'], 'evidence': '郑州'},
                             {'field': 'city', 'before': '', 'after': '郑州', 'evidence': '郑州'}]
        return result
    monkeypatch.setattr(profile_intake, 'interpret', interpret)
    response = client.post('/api/profile/interpret', json=body())
    assert response.status_code == 200
    assert '9项' not in response.json()['summary']
    assert '未保存' in response.json()['summary'] or '再保存' in response.json()['summary']
