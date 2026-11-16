"""API integration tests use isolated synthetic inputs, never real credentials."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
import threading
import time
import uuid
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import llm, model_config, providers, storage
from app.models import Job, Profile
from app.profile import parse_profile
from app.resume import extract_resume


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('JOB_DEMO_DATA_DIR', str(tmp_path))
    for name in ('JOB_LLM_API_KEY', 'JOB_LLM_MODEL', 'JOB_LLM_BASE_URL'):
        monkeypatch.delenv(name, raising=False)
    with TestClient(app) as c:
        yield c


def edit(client, patch):
    before = client.get('/api/profile').json()
    return client.patch('/api/profile', json={'patch': patch, 'expected_revision': before['revision'], 'generation': before['generation']})


def configure(client):
    result = client.put('/api/model-settings', json={'base_url': 'https://models.example/v1',
                                                    'model': 'test-model', 'api_key': 'synthetic-key-only-for-tests'})
    assert result.status_code == 200
    assert result.json()['has_key']
    assert 'synthetic-key-only-for-tests' not in result.text
    return result.json()


def chat_body(client, text, request_id=None):
    before = client.get('/api/profile').json()
    return {'message': text, 'request_id': request_id or uuid.uuid4().hex,
            'expected_revision': before['revision'], 'generation': before['generation']}


def test_persistent_profile_and_explicit_conditions(client):
    initial = client.get('/api/profile').json()
    assert initial['profile']['city'] == ''
    assert initial['profile']['salary_min'] == 0
    assert initial['profile']['accepted_schedules'] == []
    result = edit(client, {'cities': ['郑州', '洛阳'], 'target_roles': ['客服'],
                           'salary_min': 6000, 'salary_basis': 'unspecified',
                           'insurance_requirement': 'five_and_fund',
                           'accepted_schedules': [{'kind': 'custom_cycle', 'work_days': 3, 'rest_days': 1}],
                           'accept_night_shift': False, 'priorities': {'salary': 'must', 'schedule': 'prefer'}})
    assert result.status_code == 200
    stored = client.get('/api/profile').json()
    assert stored['profile']['cities'] == ['郑州', '洛阳']
    assert stored['profile']['accepted_schedules'][0]['work_days'] == 3
    assert stored['revision'] > initial['revision']
    assert stored['updated_at']
    conflict = client.patch('/api/profile', json={'patch': {'city': '北京'}, 'expected_revision': initial['revision'], 'generation': initial['generation']})
    assert conflict.status_code == 409
    assert client.get('/api/profile').json()['profile']['cities'] == ['郑州', '洛阳']


def test_no_fictional_data_in_normal_api(client):
    storage.put('jobs', [Job(id='old-demo', title='历史虚构岗位', is_demo=True, source='demo').model_dump()])
    assert client.get('/api/jobs').json()['summary']['total'] == 0
    assert client.get('/api/demo/jobs').status_code == 404
    assert client.post('/api/search', json={'source': 'demo', 'keyword': '客服'}).status_code == 422
    assert client.post('/api/jobs/import', json={'jobs': [{'title': '虚构', 'is_demo': True}]}).status_code == 422


def test_missing_key_is_not_canned_ai(client):
    result = client.post('/api/chat', json=chat_body(client, '我想在郑州找客服'))
    assert result.status_code == 428
    assert client.get('/api/profile').json()['messages'] == []
    assert client.get('/api/profile').json()['profile']['city'] == ''


def test_model_context_and_transactional_dialogue(client, monkeypatch):
    configure(client)
    assert edit(client, {'city': '郑州', 'target_roles': ['客服'], 'education': '本科'}).status_code == 200
    resume = '本科学历\n三年客服工作经验\n熟悉Excel与工单管理。\n联系方式：13800000000，test@example.com'
    assert client.post('/api/resume', files={'file': ('resume.txt', resume.encode(), 'text/plain')}).status_code == 200
    captured = []

    def respond(request):
        body = json.loads(request.content)
        captured.append(body)
        assert request.headers['authorization'] == 'Bearer synthetic-key-only-for-tests'
        last = body['messages'][-1]['content']
        output = {'reply': '已记录你要求的月薪下限，工资口径还需确认。', 'profile_patch': {}, 'search_requested': False}
        if '工资至少6000' in last:
            output['profile_patch'] = {'salary_min': {'value': 6000, 'evidence': '工资至少6000'}}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(output, ensure_ascii=False)}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, 'AsyncClient', lambda **kwargs: original(**(kwargs | {'transport': httpx.MockTransport(respond)})))
    first_body = chat_body(client, '我要求工资至少6000')
    first = client.post('/api/chat', json=first_body)
    assert first.status_code == 200
    assert first.json()['mode'] == 'llm'
    assert first.json()['profile']['salary_min'] == 6000
    duplicate = client.post('/api/chat', json=first_body)
    assert duplicate.status_code == 200
    assert len(captured) == 1
    assert len(client.get('/api/profile').json()['messages']) == 2
    assert client.post('/api/chat', json=chat_body(client, '我刚才的要求是什么？')).status_code == 200
    messages = captured[-1]['messages']
    assert messages[0]['role'] == 'system'
    assert any(m['role'] == 'assistant' for m in messages[:-1])
    encoded = json.dumps(messages, ensure_ascii=False)
    assert 'saved_profile' in encoded and 'relevant_resume_excerpt' in encoded
    assert '郑州' in encoded and 'Excel' in encoded
    local_context = json.loads(messages[1]['content'].split('\n', 1)[1])
    assert local_context['relevant_resume_excerpt'] == ''
    assert '13800000000' not in encoded and 'test@example.com' not in encoded
    assert 'synthetic-key-only-for-tests' not in encoded


@pytest.mark.parametrize('status', [401, 429, 500])
def test_model_http_errors_do_not_save_partial_changes(client, monkeypatch, status):
    configure(client)
    original = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, 'AsyncClient', lambda **kwargs: original(**(kwargs | {'transport': httpx.MockTransport(lambda _r: httpx.Response(status, json={'error': 'test failure'}))})))
    result = client.post('/api/chat', json=chat_body(client, '我想去北京找工作'))
    assert result.status_code in (401, 429, 502)
    assert client.get('/api/profile').json()['profile']['city'] == ''
    assert client.get('/api/profile').json()['messages'] == []
    assert 'synthetic-key-only-for-tests' not in result.text


def test_model_unjustified_patch_rejected(client, monkeypatch):
    configure(client)

    async def complete(*_args, **_kwargs):
        return json.dumps({'reply': '已改城市', 'profile_patch': {'city': {'value': '北京', 'evidence': '北京'}}, 'search_requested': False})

    monkeypatch.setattr(llm, '_complete', complete)
    result = client.post('/api/chat', json=chat_body(client, '北京的岗位多吗？'))
    assert result.status_code == 502
    assert client.get('/api/profile').json()['profile']['city'] == ''


@pytest.mark.parametrize('operation', ['save', 'clear'])
def test_inflight_chat_cannot_overwrite_save_or_resurrect_clear(client, monkeypatch, operation):
    configure(client)
    entered, release = threading.Event(), threading.Event()

    async def complete(*_args, **_kwargs):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(.005)
        return json.dumps({'reply': '已记录', 'profile_patch': {}, 'search_requested': False})

    monkeypatch.setattr(llm, '_complete', complete)
    body = chat_body(client, '我想找客服工作')
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, '/api/chat', json=body)
        assert entered.wait(3)
        if operation == 'save':
            assert edit(client, {'city': '杭州'}).status_code == 200
        else:
            assert client.delete('/api/data', headers={'X-Confirm-Delete': 'yes'}).status_code == 200
        release.set()
        assert future.result(timeout=4).status_code == 409
    after = client.get('/api/profile').json()
    assert after['messages'] == []
    assert after['profile']['city'] == ('杭州' if operation == 'save' else '')


def test_settings_protected_and_clear_keeps_explicit_key_control(client, tmp_path):
    configure(client)
    snapshot = client.get('/api/model-settings').json()
    assert snapshot['configured']
    assert 'api_key' not in snapshot
    for path in tmp_path.iterdir():
        if path.suffix == '.dpapi':
            assert b'synthetic-key-only-for-tests' not in path.read_bytes()
    assert client.delete('/api/data', headers={'X-Confirm-Delete': 'yes'}).status_code == 200
    assert client.get('/api/model-settings').json()['has_key']
    assert client.delete('/api/model-settings/key').status_code == 200
    assert not client.get('/api/model-settings').json()['has_key']


def test_discover_model_ids_before_model_name_is_known(client, monkeypatch):
    original = httpx.AsyncClient
    def respond(request):
        assert request.method == 'GET'
        assert request.url.path == '/v1/models'
        return httpx.Response(200, json={'data': [{'id': 'test-server-provided-model'}]})
    monkeypatch.setattr(llm.httpx, 'AsyncClient', lambda **kwargs: original(**(kwargs | {'transport': httpx.MockTransport(respond)})))
    response = client.post('/api/model-settings/discover', json={'base_url': 'https://models.example/v1', 'api_key': 'synthetic-discovery-key'})
    assert response.status_code == 200
    assert response.json()['models'] == ['test-server-provided-model']
    assert response.json()['settings']['model'] == 'test-server-provided-model'
    assert response.json()['settings']['has_key']
    assert 'synthetic-discovery-key' not in response.text


def test_user_import_and_draft_stays_separate_from_collection(client):
    assert edit(client, {'city': '郑州', 'target_roles': ['客服'], 'salary_min': 6000,
                         'salary_basis': 'base', 'insurance_requirement': 'five_and_fund'}).status_code == 200
    row = {'title': '售后客服', 'company': '测试招聘原文结构', 'city': '郑州', 'salary_text': '6K-8K',
           'description': '负责售后工单，周末双休，缴纳五险，招聘邮箱 jobs@example.com，固定底薪6000元/月。',
           'source_url': 'https://www.zhipin.com/job_detail/test-only.html'}
    result = client.post('/api/jobs/import', json={'jobs': [row]})
    assert result.status_code == 200
    job = result.json()['jobs'][0]
    assert job['source'] == 'import'
    assert job['completeness'] == 'user_provided'
    assert job['analysis']['verdict'] == 'needs_confirmation'
    draft = client.post('/api/jobs/' + job['id'] + '/application').json()
    assert '未发送' in draft['notice']
    assert draft['contacts'][0]['value'] == 'jobs@example.com'
    assert client.get('/api/jobs').json()['summary']['demo'] == 0


def test_async_collection_and_honest_partial_status(client, monkeypatch):
    assert edit(client, {'city': '郑州', 'target_roles': ['客服']}).status_code == 200

    def plan(profile, keyword='', sources=None):
        return {'sources': sources or ['boss'], 'queries': [{'source': s, 'keyword': keyword or '客服', 'city': profile.city} for s in sources or ['boss']]}

    def collect(source, _profile, _query, emit, cancelled):
        if source == 'liepin':
            return {'jobs': [], 'status': 'restricted', 'warnings': ['测试源需要人工验证'], 'diagnostics': {'stage': 'login'}}
        row = Job(id='test-captured', title='客服', company='测试输入非真实联调', city='郑州',
                  source='boss', source_url='https://www.zhipin.com/job_detail/test-only.html',
                  description='测试夹具：客服工作要求、工资及福利等正文仅用于自动化验证。', completeness='full_jd')
        emit({'stage': 'details', 'jobs': [row], 'progress': 80})
        return {'jobs': [row], 'status': 'success', 'warnings': [], 'diagnostics': {'list_count': 1, 'detail_count': 1}}

    monkeypatch.setattr(providers, 'build_search_plan', plan)
    monkeypatch.setattr(providers, 'collect_source', collect)
    result = client.post('/api/search', json={'sources': ['boss', 'liepin'], 'keyword': '客服'})
    assert result.status_code == 202
    task_id = result.json()['task_id']
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        state = client.get('/api/tasks/' + task_id).json()
        if state['status'] not in ('queued', 'running'):
            break
        time.sleep(.01)
    assert state['status'] == 'partial'
    assert state['summary']['total'] == 1
    assert state['sources'][1]['status'] == 'restricted'
    assert not client.get('/api/jobs').json()['stale']


def test_upload_formats_and_persistence(client):
    assert client.post('/api/resume', files={'file': ('x.exe', b'junk')}).status_code == 422
    assert client.post('/api/resume', files={'file': ('x.pdf', b'not a pdf')}).status_code == 422
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>候选人简历，本科学历，三年客服工作经验，熟悉Excel及工单系统。</w:t></w:r></w:p></w:body></w:document>'
    doc = io.BytesIO()
    with zipfile.ZipFile(doc, 'w') as z:
        z.writestr('word/document.xml', xml)
    text, _ = extract_resume(doc.getvalue(), 'resume.docx')
    assert '客服' in text
    result = client.post('/api/resume', files={'file': ('resume.docx', doc.getvalue())})
    assert result.status_code == 200
    assert client.get('/api/profile').json()['profile']['resume_text'] == text
    assert client.get('/api/profile').json()['profile']['experience_years'] == 3
    assert edit(client, {'city': '郑州'}).status_code == 200
    assert client.get('/api/profile').json()['profile']['resume_text'] == text


def test_cross_origin_and_invalid_source_rejected(client):
    assert client.post('/api/chat', headers={'Origin': 'https://evil.example'}, json={'message': 'test'}).status_code == 403
    assert client.get('/api/profile', headers={'Host': 'evil.example'}).status_code == 400
    assert client.post('/api/jobs/import', json={'jobs': [{'title': 'x', 'source_url': 'javascript:alert(1)'}]}).status_code == 422


def test_real_pdf_text_upload(client):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 72 720 Td (Candidate: Python, SQL, customer support experience.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    content = io.BytesIO()
    writer.write(content)
    result = client.post('/api/resume', files={'file': ('resume.pdf', content.getvalue(), 'application/pdf')})
    assert result.status_code == 200
    assert 'Python' in result.json()['preview']
