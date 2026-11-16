import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.main import app
from app import analysis_stream, providers


class TokenBody(httpx.AsyncByteStream):
    def __init__(self, complete=True, pause=None):
        self.complete, self.pause = complete, pause

    async def __aiter__(self):
        payloads = [
            {'choices': [{'delta': {'reasoning_content': '内部思考不展示'}, 'finish_reason': None}]},
            {'choices': [{'delta': {'content': '这份岗位的薪资有原文依据。'}, 'finish_reason': None}]},
            {'choices': [{'delta': {'content': '五险一金和休息安排仍需核对。'}, 'finish_reason': None}]},
        ]
        for index, payload in enumerate(payloads):
            encoded = ('data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n').encode()
            # Split UTF-8 and SSE frames across arbitrary transport boundaries.
            yield encoded[:13]
            await asyncio.sleep(0)
            yield encoded[13:]
            if index == 1 and self.pause:
                entered, release = self.pause
                entered.set()
                while not release.is_set():
                    await asyncio.sleep(.005)
        if self.complete:
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            yield b'data: [DONE]\n\n'


def frames(text):
    result = []
    for frame in text.split('\n\n'):
        lines = frame.splitlines()
        event = next((s[7:] for s in lines if s.startswith('event: ')), None)
        data = '\n'.join(s[6:] for s in lines if s.startswith('data: '))
        if event and data:
            result.append((event, json.loads(data)))
    return result


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('JOB_DEMO_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('JOB_LLM_API_KEY', raising=False)
    monkeypatch.delenv('JOB_LLM_MODEL', raising=False)
    with TestClient(app) as c:
        yield c


def setup(client):
    assert client.put('/api/model-settings', json={'base_url': 'https://test.example/v1', 'model': 'test', 'api_key': 'synthetic-stream-key'}).status_code == 200
    snap = client.get('/api/profile').json()
    edited = client.patch('/api/profile', json={'patch': {'city': '郑州', 'target_roles': ['客服'], 'salary_min': 6000}, 'expected_revision': snap['revision'], 'generation': snap['generation']})
    assert edited.status_code == 200
    rows = [{'title': title, 'company': company, 'city': '郑州', 'salary_text': '6000-8000元/月',
             'description': '用于隔离自动化测试的招聘正文：负责售后客服工作，具体休息、薪资口径及公积金均需核对。',
             'source_url': 'https://www.zhipin.com/job_detail/test-' + str(i) + '.html'}
            for i, (title, company) in enumerate([('客服甲', '测试甲公司'), ('客服乙', '测试乙公司')])]
    result = client.post('/api/jobs/import', json={'jobs': rows})
    assert result.status_code == 200
    return [j['id'] for j in result.json()['jobs']]


def body(client, job_ids=None):
    snapshot = client.get('/api/profile').json()
    return {'message': '这份岗位适合我吗？', 'request_id': uuid.uuid4().hex,
            'expected_revision': snapshot['revision'], 'generation': snapshot['generation'], 'job_ids': job_ids or []}


def transport(monkeypatch, captured, complete=True, pause=None):
    original = httpx.AsyncClient

    def respond(request):
        data = json.loads(request.content)
        captured.append(data)
        assert data['stream'] is True
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, stream=TokenBody(complete, pause))

    monkeypatch.setattr(analysis_stream.httpx, 'AsyncClient', lambda **kwargs: original(**(kwargs | {'transport': httpx.MockTransport(respond)})))


@pytest.mark.parametrize('false_claim', [True, False])
def test_schedule_consistency_checked_before_display_and_history_commit(client, monkeypatch, false_claim):
    setup(client)
    assert client.patch('/api/profile', json={'patch': {'accepted_schedules': [{'kind': 'weekly_double'}]}}).status_code == 200
    before = client.get('/api/profile').json()
    text = ('先看原文。大小周通常满足此要求，但需确认具体频率。' if false_claim
            else '大小周包含单休周，不能保证每周休两天。标题和正文口径冲突，仍需确认。')

    class GuardedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            for char in text:
                payload = {'choices': [{'delta': {'content': char}, 'finish_reason': None}]}
                yield ('data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n').encode()
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            yield b'data: [DONE]\n\n'

    original = httpx.AsyncClient
    def respond(request):
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, stream=GuardedBody())
    monkeypatch.setattr(analysis_stream.httpx, 'AsyncClient', lambda **kwargs: original(**(kwargs | {'transport': httpx.MockTransport(respond)})))
    response = client.post('/api/chat/stream', json=body(client))
    events = frames(response.text)
    if false_claim:
        assert events[-1][0] == 'error'
        assert events[-1][1]['code'] == 'inconsistent_schedule_claim'
        assert '通常满足' not in ''.join(data['text'] for name, data in events if name == 'delta')
        assert not any(name == 'done' for name, _ in events)
        assert client.get('/api/profile').json() == before
    else:
        assert events[-1][0] == 'done'
        assert events[-1][1]['reply'] == text
        assert len(client.get('/api/profile').json()['messages']) == 2


def test_true_stream_selected_context_atomic_history_and_replay(client, monkeypatch):
    ids = setup(client)
    captured = []
    transport(monkeypatch, captured)
    monkeypatch.setattr(providers, 'collect_source', lambda *a, **k: pytest.fail('Analysis must not start a recruitment query'))
    before = client.get('/api/profile').json()
    request = body(client, [ids[1]])
    response = client.post('/api/chat/stream', json=request)
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')
    events = frames(response.text)
    assert [name for name, _ in events] == ['start', 'delta', 'delta', 'done']
    assert '内部思考' not in response.text
    done = events[-1][1]
    assert done['mode'] == 'llm_stream'
    assert done['profile'] == before['profile']
    assert done['messages'][-1]['job_ids'] == [ids[1]]
    assert done['reply'] == '这份岗位的薪资有原文依据。五险一金和休息安排仍需核对。'
    local = json.loads(captured[0]['messages'][1]['content'].split('\n', 1)[1])
    assert [j['id'] for j in local['jobs']] == [ids[1]]
    assert '不修改画像' in captured[0]['messages'][0]['content']
    again = client.post('/api/chat/stream', json=request)
    replay = frames(again.text)
    assert len(captured) == 1
    assert [name for name, _ in replay] == ['done']
    assert replay[0][1]['replayed']
    assert len(client.get('/api/profile').json()['messages']) == 2


def test_incomplete_upstream_keeps_partial_visible_but_does_not_save(client, monkeypatch):
    setup(client)
    captured = []
    transport(monkeypatch, captured, complete=False)
    response = client.post('/api/chat/stream', json=body(client))
    events = frames(response.text)
    assert any(name == 'delta' for name, _ in events)
    assert events[-1][0] == 'error'
    assert events[-1][1]['code'] == 'incomplete_stream'
    assert not any(name == 'done' for name, _ in events)
    assert client.get('/api/profile').json()['messages'] == []


@pytest.mark.parametrize('action', ['edit', 'clear'])
def test_midstream_changes_cannot_be_overwritten(client, monkeypatch, action):
    setup(client)
    captured = []
    entered, release = threading.Event(), threading.Event()
    transport(monkeypatch, captured, pause=(entered, release))
    request = body(client)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, '/api/chat/stream', json=request)
        assert entered.wait(3)
        if action == 'edit':
            snapshot = client.get('/api/profile').json()
            assert client.patch('/api/profile', json={'patch': {'city': '杭州'}, 'expected_revision': snapshot['revision'], 'generation': snapshot['generation']}).status_code == 200
        else:
            assert client.delete('/api/data', headers={'X-Confirm-Delete': 'yes'}).status_code == 200
        release.set()
        events = frames(future.result(timeout=5).text)
    assert events[-1][0] == 'error'
    assert events[-1][1]['code'] == 'revision_conflict'
    snapshot = client.get('/api/profile').json()
    assert snapshot['messages'] == []
    assert snapshot['profile']['city'] == ('杭州' if action == 'edit' else '')


def test_stream_requires_real_key_and_existing_selection(client):
    assert client.post('/api/chat/stream', json=body(client)).status_code == 428
    setup(client)
    assert client.post('/api/chat/stream', json=body(client, ['not-a-real-id'])).status_code == 404


def test_client_disconnect_does_not_commit_partial(client, monkeypatch):
    setup(client)
    transport(monkeypatch, [])
    calls = 0

    async def disconnected(_self):
        nonlocal calls
        calls += 1
        return calls > 1

    monkeypatch.setattr(Request, 'is_disconnected', disconnected)
    response = client.post('/api/chat/stream', json=body(client))
    assert not any(name == 'done' for name, _ in frames(response.text))
    assert client.get('/api/profile').json()['messages'] == []


def test_secret_filter_does_not_leak_secrets_split_across_deltas():
    redact = analysis_stream.SecretFilter('synthetic-stream-key')
    result = ''.join(redact.feed(part) for part in ['正常文字synt', 'hetic-stream-', 'key结束']) + redact.finish()
    assert 'synthetic-stream-key' not in result
    assert '[密钥已隐藏]' in result
