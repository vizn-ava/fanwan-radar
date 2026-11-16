import ssl

from app.network import tls_context
from app.main import _profile_fingerprint
from app.models import Profile, Job, Schedule
from app.matching import analyze
from app.llm import completion_options
from app import storage, search_tasks
from app.main import app
from fastapi.testclient import TestClient


def test_reuses_certificate_context_without_disabling_validation():
    first = tls_context()
    assert first is tls_context()
    assert first.verify_mode == ssl.CERT_REQUIRED
    assert first.check_hostname is True


def test_search_scope_tracks_conditions_not_chat_revision_or_display_name():
    profile = Profile(city='郑州', target_roles=['客服'])
    original = _profile_fingerprint(profile)
    assert _profile_fingerprint(profile.model_copy(update={'name': 'display name'})) == original
    assert _profile_fingerprint(profile.model_copy(update={'salary_min': 6000})) != original
    assert _profile_fingerprint(profile.model_copy(update={'cities': ['北京']})) != original


def test_fast_answer_option_only_for_supported_native_provider():
    credentials = {'base_url': 'https://test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1',
                   'model': 'qwen3.8-flash'}
    assert completion_options(credentials) == {'enable_thinking': False}
    assert completion_options(credentials | {'base_url': 'https://example.com/v1'}) == {}
    assert completion_options(credentials | {'model': 'a-different-model'}) == {}
    assert completion_options(credentials | {'base_url': 'https://dashscope.aliyuncs.com.attacker.example/compatible-mode/v1'}) == {}


def test_task_api_does_not_confuse_saved_analysis_with_edited_conditions(tmp_path, monkeypatch):
    monkeypatch.setenv('JOB_DEMO_DATA_DIR', str(tmp_path))
    before = storage.snapshot()
    task = {'id': 'scope-check', 'status': 'completed', 'jobs': [], 'warnings': [],
            'plan': {'profile_revision': before['revision'],
                     'profile_fingerprint': _profile_fingerprint(before['profile'])}}
    monkeypatch.setattr(search_tasks, 'get', lambda _task_id: task)
    with TestClient(app) as client:
        assert not client.get('/api/tasks/scope-check').json().get('profile_changed')
        storage.commit_chat({}, 'analysis question', 'analysis reply', before['revision'],
                            before['generation'], 'scope-chat-check', mode='llm_stream')
        assert not client.get('/api/tasks/scope-check').json().get('profile_changed')
        storage.update_profile({'city': '郑州'})
        assert client.get('/api/tasks/scope-check').json()['profile_changed'] is True


def test_value_edits_preserve_existing_priority_choices(tmp_path, monkeypatch):
    monkeypatch.setenv('JOB_DEMO_DATA_DIR', str(tmp_path))
    storage.update_profile({'salary_min': 6000, 'priorities': {'salary': 'prefer'}})
    state = storage.update_profile({'salary_min': 7000})
    assert state['profile'].priorities['salary'] == 'prefer'
    storage.update_profile({'city': '郑州', 'priorities': {'location': 'ignore'}})
    state = storage.update_profile({'city': '深圳'})
    assert state['profile'].priorities['location'] == 'ignore'
    state = storage.update_profile({'insurance_requirement': 'five_and_fund'})
    assert state['profile'].priorities['insurance'] == 'must'


def test_title_and_body_rest_conflict_stays_unknown():
    job = Job(id='title-conflict-test', title='客服有双休', company='Synthetic test company',
              description='工作安排为单双休，请以面试时双方确认的排班为准。', completeness='full_jd')
    report = analyze(job, Profile(accepted_schedules=[Schedule(kind='weekly_double')]))
    check = next(c for c in report['checks'] if c['key'] == 'schedule')
    assert check['status'] == 'unknown'


def test_omitting_fund_in_body_does_not_negate_explicit_title_promise():
    job = Job(id='title-fund-test', title='客服 五险一金', company='Synthetic test company',
              description='公司正常缴纳五险，具体入职时间双方协商。', completeness='full_jd')
    report = analyze(job, Profile(insurance_requirement='five_and_fund'))
    check = next(c for c in report['checks'] if c['key'] == 'insurance')
    assert check['status'] == 'pass'
