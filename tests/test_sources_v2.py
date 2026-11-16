"""Synthetic source tests: no real profile, database, key or browser access."""
import json
from types import SimpleNamespace

import httpx
import pytest

from app.models import Profile
from app import providers as p
from app import collector_worker as w
from app.query_planner import build_search_plan


def raw_job(**patch):
    return dict({'encryptJobId': 'synthetic_encrypted123', 'jobName': 'Python开发',
                 'salaryDesc': '5-10K', 'brandName': '合成测试公司', 'cityName': '郑州',
                 'jobDegree': '大专', 'jobExperience': '1-3年', 'skills': ['Python'],
                 'welfareList': ['五险一金']}, **patch)


def test_plan_multiple_roles_cities_wide_salary_no_exact_degree():
    profile = Profile(cities=['郑州', '上海'], target_roles=['Python', '数据分析'],
                      education='本科', experience_years=5, salary_min=6000,
                      salary_max=15000, salary_basis='advertised')
    plan = build_search_plan(profile)
    queries = plan['sources'][0]['queries']
    assert len(queries) == 4
    assert queries[0]['platform_filters'] == {}
    assert any('多个薪资档位' in warning for warning in plan['warnings'])
    assert all('degree' not in q['platform_filters'] and 'experience' not in q['platform_filters'] for q in queries)
    assert plan['post_filters']['experience']['values']['experience_years'] == 5


@pytest.mark.parametrize('patch', [{'salary_basis': 'base'}, {'salary_period': 'year'},
                                  {'priorities': {'salary': 'prefer'}},
                                  {'confirmation_required': ['salary_min']}])
def test_unfit_salary_not_platform_filtered(patch):
    values = dict(salary_min=6000, salary_basis='advertised', target_roles=['开发'])
    values.update(patch)
    assert build_search_plan(Profile(**values))['sources'][0]['queries'][0]['platform_filters'] == {}


def test_explicit_false_and_pending_conditions_retained():
    profile = Profile(accept_night_shift=False, allow_remote=False, max_daily_hours=8,
                      confirmation_required=['night_shift'], priorities={'hours': 'prefer'})
    post = build_search_plan(profile)['post_filters']
    assert post['night_shift']['values']['accept_night_shift'] is False
    assert post['night_shift']['pending_confirmation']
    assert post['hours']['priority'] == 'prefer'
    assert 'insurance' not in post


def test_no_keyword_no_accidental_broad_scan():
    plan = build_search_plan(Profile(), sources=['boss', 'demo', 'public'])
    assert not plan['sources'][0]['queries']
    assert not plan['sources'][1]['enabled']
    assert plan['warnings']
    assert 'salary' not in plan['post_filters']


def test_normalize_rich_api_fields_and_detail_skills():
    job = p.normalize_boss(raw_job(), {'jd': '职责：Python开发。要求：大专及以上，1年工作经验。', 'tags': ['SQL']})
    assert job.company == '合成测试公司'
    assert job.education == '大专'
    assert job.experience_min == 1
    assert job.skills == ['Python', 'SQL']
    assert job.completeness == 'full_jd'
    assert job.salary_period == 'month'
    assert job.salary_source == 'boss_list_api'
    assert job.posted_at == ''


def test_mapped_tags_not_recruiter_title_or_hash_link():
    row = {'title': '测试', 'job_id': 'deadbeef1234', 'boss_name': '公司',
           'tags': '3-5年 | 本科', 'job_link': 'https://www.zhipin.com/job_detail/actual123.html?securityId=SECRET&lid=SECRET#frag'}
    job = p.normalize_boss(row, {})
    assert job.experience_min == 3 and job.education == '本科'
    assert job.source_url == 'https://www.zhipin.com/job_detail/actual123.html'
    assert job.completeness == 'list_only'
    del row['job_link']
    with pytest.raises(p.SourceUnavailable) as caught:
        p.normalize_boss(row, {})
    assert caught.value.status == 'schema_error'


@pytest.mark.parametrize('url', ['https://www.zhipin.com.evil.test/job_detail/a.html',
                               'https://user:secret@www.zhipin.com/job_detail/a.html',
                               'javascript:alert(1)', 'https://www.zhipin.com/web/user/'])
def test_unsafe_links_rejected(url):
    assert p.clean_job_url(url) == ''


@pytest.mark.parametrize('salary,period', [('200-300元/天', 'day'), ('20-30万/年', 'year'), ('30元/小时', 'hour')])
def test_non_monthly_salary_not_monthly_numbers(salary, period):
    job = p.normalize_boss(raw_job(salaryDesc=salary), {})
    assert job.salary_period == period
    # Values, when parsed, remain in their native day/year/hour period.
    if period == 'day':
        assert job.salary_min in (None, 200)
    if period == 'year':
        assert job.salary_min in (None, 200000)


def test_contacts_only_explicit():
    assert not p.public_contacts('手机号掩码 138****1234，编号 13800138000')
    assert [c.kind for c in p.public_contacts('招聘电话：13800138000 邮箱 hr@example.test')] == ['email', 'phone']


@pytest.mark.parametrize('payload,status', [({}, 'schema_error'), ({'code': 7}, 'unauthenticated'),
                                         ({'code': 37, 'message': 'secret'}, 'restricted'),
                                         ({'code': 0, 'zpData': {}}, 'schema_error'),
                                         ([], 'schema_error')])
def test_list_validation(payload, status):
    with pytest.raises(p.SourceUnavailable) as caught:
        w.validate_list(payload)
    assert caught.value.status == status
    assert 'secret' not in json.dumps(caught.value.diagnostics)
    assert w.validate_list({'code': 0, 'zpData': {'jobList': []}}) == []


def test_protocol_validation_not_tcp_or_login(monkeypatch):
    data = {'Browser': 'Fake', 'Protocol-Version': '1.3',
            'webSocketDebuggerUrl': 'ws://127.0.0.1:9222/devtools/browser/synthetic'}

    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url): return httpx.Response(200, json=data, request=httpx.Request('GET', url))

    monkeypatch.setattr(p.httpx, 'Client', Client)
    monkeypatch.setenv('JOB_CDP_PORT', '9222')
    assert p.boss_available()
    assert '登录' in p.source_statuses()[0]['description']
    data['webSocketDebuggerUrl'] = 'ws://example.test:9222/devtools/browser/x'
    assert not p.boss_available()
    data.clear()
    assert not p.boss_available()


@pytest.fixture
def fake_browser(monkeypatch):
    state = SimpleNamespace(detail_error=None, cancel=False, closed=False)

    class Browser:
        def __init__(self, cancelled): self.cancelled = cancelled
        def open(self): pass
        def check(self):
            if self.cancelled(): raise p.SourceUnavailable('取消', 'cancelled', 'cancel')
        def list_response(self, url): return [raw_job()]
        def detail(self, url, upstream):
            if state.detail_error: raise state.detail_error
            return {'jd': '合成完整岗位描述，要求 Python开发经验，职责清晰。', 'tags': ['SQL']}
        def close(self): state.closed = True

    upstream = SimpleNamespace(map_api_job=lambda r: r, load_local_city_map=lambda: ({'郑州': '101180100'}, {}))
    monkeypatch.setattr(w, 'upstream_components', lambda: upstream)
    monkeypatch.setattr(w, 'PassiveCDP', Browser)
    monkeypatch.setattr(w, 'pause', lambda *args: None)
    return state


def test_incremental_list_then_full_jd(fake_browser):
    events = []
    result = w.collect_source('boss', Profile(), {'keyword': 'Python', 'city': '郑州'}, events.append, lambda: False)
    assert result['status'] == 'success'
    assert result['diagnostics']['full_jd'] == 1
    updates = [e['jobs'][0]['completeness'] for e in events if 'jobs' in e]
    assert updates == ['list_only', 'full_jd']
    assert fake_browser.closed


def test_restriction_preserves_received_partials(fake_browser):
    fake_browser.detail_error = p.SourceUnavailable('本人验证', 'restricted', 'detail')
    result = w.collect_source('boss', Profile(), {'keyword': 'Python'}, lambda e: None, lambda: False)
    assert result['status'] == 'partial' and len(result['jobs']) == 1
    assert result['diagnostics']['failure_status'] == 'restricted'
    assert result['jobs'][0].completeness == 'list_only'


def test_cancel_after_list_retains_record(fake_browser):
    events = []
    def emit(event):
        events.append(event)
        if 'jobs' in event:
            fake_browser.cancel = True
    # A real pause checks cancellation; keep that behavior in this fixture.
    fake_browser.detail_error = p.SourceUnavailable('取消', 'cancelled', 'cancel')
    result = w.collect_source('boss', Profile(), {'keyword': 'Python'}, emit, lambda: fake_browser.cancel)
    assert result['status'] == 'cancelled' and len(result['jobs']) == 1
    assert fake_browser.closed


def test_disabled_sources_and_pre_cancel_no_browser(monkeypatch):
    def fail(*a): raise AssertionError('must not open browser')
    monkeypatch.setattr(w, 'PassiveCDP', fail)
    for source in ('demo', 'public', 'liepin', 'zhaopin'):
        assert w.collect_source(source, Profile(), {}, lambda e: None, lambda: False)['status'] == 'unavailable'
    assert w.collect_source('boss', Profile(), {}, lambda e: None, lambda: True)['status'] == 'cancelled'


@pytest.mark.parametrize('page,status', [({'url': 'https://www.zhipin.com/web/user/'}, 'unauthenticated'),
                                       ({'text': '访问异常'}, 'restricted'),
                                       ({'title': '安全中心-验证码'}, 'restricted'),
                                       ({'title': 'Security Verification'}, 'restricted'),
                                       ({'text': '职位列表'}, None)])
def test_gate_detection(page, status):
    assert w.classify_page(page) == status
