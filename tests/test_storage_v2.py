import importlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError

from app import storage
from app.models import ChatRequest, Job, Profile, ProfileUpdate, Schedule, SearchRequest


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv('JOB_DEMO_DATA_DIR', str(tmp_path))
    return tmp_path


def legacy_db(directory, profile):
    with sqlite3.connect(directory / 'local.sqlite3') as db:
        db.execute('CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('INSERT INTO state VALUES (?,?)', ('profile', json.dumps(profile)))
        db.execute('INSERT INTO state VALUES (?,?)', ('messages', json.dumps([{'role': 'user', 'content': 'synthetic'}])))


def test_safe_defaults_and_rich_models():
    p = Profile()
    assert (p.city, p.salary_min, p.salary_basis) == ('', 0, 'unspecified')
    assert p.accepted_schedules == []
    assert p.insurance_requirement == 'any'
    assert p.require_double_rest is p.require_housing_fund is p.allow_remote is None
    assert ChatRequest(message='a').request_id != ChatRequest(message='a').request_id
    assert SearchRequest().sources == ['boss']
    assert SearchRequest(source='public').sources == ['public']
    assert Job(title='synthetic').posted_at == ''
    with pytest.raises(ValidationError):
        SearchRequest(sources=['demo'])
    with pytest.raises(ValidationError):
        Schedule(kind='rotation', fixed_weekdays=[8])
    with pytest.raises(ValidationError):
        Profile(work_start='25:00')


def test_legacy_defaults_preserved_but_pending(isolated_store):
    legacy = {'city': '\u90d1\u5dde', 'salary_min': 6000, 'salary_basis': 'base',
              'require_double_rest': True, 'require_housing_fund': True,
              'allow_remote': False, 'resume_text': 'synthetic resume', 'skills': ['SQL']}
    legacy_db(isolated_store, legacy)
    state = storage.snapshot()
    p = state['profile']
    assert isinstance(p, Profile)
    assert p.city == legacy['city'] and p.resume_text == legacy['resume_text']
    assert p.accepted_schedules[0].kind == 'weekly_double'
    assert p.insurance_requirement == 'five_and_fund'
    assert {'city', 'salary_min', 'accepted_schedules', 'insurance_requirement'} <= set(p.confirmation_required)
    assert p.priorities['salary'] == p.priorities['schedule'] == 'ignore'
    assert state['messages'][0]['content'] == 'synthetic'
    assert storage.snapshot() == state


def test_legacy_nondefaults_and_explicit_sources_remain_known(isolated_store):
    legacy_db(isolated_store, {'city': 'Example City', 'salary_min': 8500,
                              'require_double_rest': True, 'require_housing_fund': True,
                              'field_sources': {'require_double_rest': 'manual',
                                                'require_housing_fund': 'chat'}})
    p = storage.profile()
    assert p.confirmation_required == []
    assert all(p.priorities[key] == 'must' for key in ('location', 'salary', 'schedule', 'insurance'))


def test_partial_updates_preserve_hidden_resume_and_reload():
    first = storage.update_profile({'resume_text': 'synthetic private text', 'skills': ['Python'],
                                    'experience_years': 4, 'notes': 'synthetic note'}, source='resume')
    second = storage.update_profile({'city': 'Example City', 'salary_min': 9000},
                                    first['revision'], first['generation'])
    p = second['profile']
    assert (p.resume_text, p.skills, p.experience_years, p.notes) == ('synthetic private text', ['Python'], 4, 'synthetic note')
    assert p.field_sources['resume_text'] == 'resume'
    assert p.field_sources['city'] == 'manual'
    assert p.priorities['salary'] == 'must'
    assert second['revision'] == first['revision'] + 1
    assert importlib.reload(storage).snapshot() == second


def test_user_confirmation_resolves_legacy_ambiguity(isolated_store):
    legacy_db(isolated_store, {'city': '\u90d1\u5dde', 'require_double_rest': True})
    before = storage.snapshot()
    after = storage.update_profile({'city': '\u90d1\u5dde', 'accepted_schedules': [{'kind': 'weekend_double'}]},
                                   before['revision'], before['generation'])
    p = after['profile']
    assert 'city' not in p.confirmation_required
    assert 'accepted_schedules' not in p.confirmation_required
    assert p.priorities['location'] == p.priorities['schedule'] == 'must'


def test_concurrent_stale_writes_have_one_winner():
    initial = storage.snapshot()
    barrier = Barrier(2)

    def writer(city):
        barrier.wait(timeout=5)
        try:
            return storage.update_profile({'city': city}, initial['revision'], initial['generation'])
        except storage.ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(writer, ['One', 'Two']))
    assert sum(result is not None for result in results) == 1
    assert storage.snapshot()['revision'] == initial['revision'] + 1


@pytest.mark.parametrize('patch', [
    {'revision': 100}, {'field_sources': {'city': 'manual'}},
    {'confirmation_required': []}, {'salary_min': -1}, {'salary_min': '9000'},
    {'max_daily_hours': float('nan')}, {'priorities': {'invented': 'must'}},
    {'priorities': {'salary': 'invalid'}}, {'accepted_schedules': [{'kind': 'invented'}]},
    {'work_end': 'wrong'}, {'resume_text': ''},
])
def test_invalid_patch_is_atomic(patch):
    before = storage.snapshot()
    with pytest.raises((ValueError, ValidationError)):
        storage.update_profile(patch, before['revision'], before['generation'])
    assert storage.snapshot() == before


def test_protected_profile_update_rejected():
    with pytest.raises(ValidationError):
        ProfileUpdate(patch={'field_sources': {'salary_min': 'chat'}})


def test_salary_bounds_and_priority_merge():
    first = storage.update_profile({'salary_min': 5000, 'salary_max': 8000,
                                    'city': 'Example', 'priorities': {'salary': 'prefer'}})
    with pytest.raises(ValidationError):
        storage.update_profile({'salary_min': 9000})
    second = storage.update_profile({'priorities': {'location': 'prefer'}})
    assert second['profile'].priorities['salary'] == 'prefer'
    assert second['profile'].priorities['location'] == 'prefer'
    assert storage.snapshot()['revision'] == first['revision'] + 1


def test_duplicate_chat_returns_original_after_reload():
    initial = storage.snapshot()
    result = storage.commit_chat({'city': 'Example'}, 'synthetic question', 'synthetic reply',
                                 initial['revision'], initial['generation'], 'request-one')
    storage.update_profile({'notes': 'later edit'})
    duplicate = storage.commit_chat({'city': 'Wrong'}, 'other', 'other',
                                    initial['revision'], initial['generation'], 'request-one')
    assert duplicate == result
    assert importlib.reload(storage).cached_chat('request-one') == result
    assert storage.profile().city == 'Example'
    assert len(storage.snapshot()['messages']) == 2


def test_concurrent_duplicate_chat_commits_once():
    initial = storage.snapshot()
    barrier = Barrier(2)

    def commit(_):
        barrier.wait(timeout=5)
        return storage.commit_chat({}, 'question', 'reply', initial['revision'], initial['generation'], 'same')

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(commit, range(2)))
    assert results[0] == results[1]
    assert len(storage.snapshot()['messages']) == 2
    assert storage.snapshot()['revision'] == initial['revision'] + 1


def test_chat_conflict_does_not_append_or_cache():
    initial = storage.snapshot()
    storage.update_profile({'city': 'New'})
    with pytest.raises(storage.ConflictError):
        storage.commit_chat({}, 'question', 'reply', initial['revision'], initial['generation'], 'stale')
    assert storage.snapshot()['messages'] == []
    assert storage.cached_chat('stale') is None


def test_clear_preserves_configuration_and_cancels_inflight():
    storage.put('model_settings', {'model': 'synthetic-model', 'base_url': 'https://example.invalid'})
    initial = storage.snapshot()
    storage.commit_chat({'city': 'Example'}, 'question', 'reply', initial['revision'], initial['generation'], 'old')
    storage.save_search([{'title': 'Synthetic'}], {'source': 'import'}, initial['generation'])
    cleared = storage.clear()
    assert cleared['generation'] == initial['generation'] + 1
    assert cleared['profile'] == Profile()
    assert cleared['messages'] == []
    assert storage.get('jobs', []) == [] and storage.get('search_meta', {}) == {}
    assert storage.get('model_settings', {})['model'] == 'synthetic-model'
    assert storage.cached_chat('old') is None
    with pytest.raises(storage.ConflictError):
        storage.update_profile({'city': 'Resurrected'}, generation=initial['generation'])
    with pytest.raises(storage.ConflictError):
        storage.commit_chat({}, 'question', 'reply', initial['revision'], initial['generation'], 'old')
    with pytest.raises(storage.ConflictError):
        storage.save_search([{'title': 'Late'}], {}, initial['generation'])
    assert storage.snapshot() == cleared


def test_search_persists_metadata_and_rolls_back_invalid_results():
    generation = storage.snapshot()['generation']
    storage.save_search([{'title': 'Synthetic', 'source': 'import', 'social_insurance': True}],
                        {'source': 'import', 'warnings': ['synthetic warning']}, generation)
    assert storage.get('jobs', [])[0]['social_insurance'] is True
    with pytest.raises(ValidationError):
        storage.save_search([{'title': 'Invalid', 'weekly_rest_days': 8}], {}, generation)
    assert storage.get('jobs', [])[0]['title'] == 'Synthetic'
    assert storage.get('search_meta', {})['warnings'] == ['synthetic warning']


def test_connections_close_explicitly():
    with storage.connection() as db:
        db.execute('SELECT 1')
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute('SELECT 1')


def test_rich_profile_and_job_roundtrip():
    patch = {
        'cities': ['One', 'Two'], 'districts': ['District'], 'salary_min': 100,
        'salary_max': 200, 'salary_period': 'hour', 'salary_basis': 'advertised',
        'insurance_requirement': 'five_insurances', 'insurance_during_probation': True,
        'fund_base_min': 5000, 'accepted_schedules': [
            {'kind': 'custom_cycle', 'work_days': 4, 'rest_days': 2, 'fixed_weekdays': [6, 7]}],
        'max_daily_hours': 8.5, 'accept_night_shift': False, 'accept_rotating_shift': False,
        'accept_overtime': False, 'work_start': '09:00', 'work_end': '18:00',
        'employment_types': ['full time'], 'industries': ['software'],
        'excluded_terms': ['sales'], 'accept_outsourcing': False, 'accept_dispatch': False,
        'allow_remote': True, 'allow_relocation': False, 'max_commute_minutes': 45,
        'priorities': {'commute': 'prefer', 'industry': 'ignore'},
    }
    saved = storage.update_profile(patch)
    reloaded = importlib.reload(storage).snapshot()
    assert reloaded == saved
    assert reloaded['profile'].accept_night_shift is False
    assert reloaded['profile'].priorities['night_shift'] == 'must'
    assert reloaded['profile'].priorities['industry'] == 'ignore'
    job = Job(title='Synthetic', source='zhaopin', social_insurance=True,
              insurance_during_probation=False, fund_base=5000, salary_period='year',
              salary_source='synthetic evidence', schedule_kind='rotation', cycle_work_days=4,
              cycle_rest_days=2, weekly_rest_days=2, fixed_rest_weekdays=[6, 7],
              work_hours_daily=8, night_shift=False, rotating_shift=True, overtime=None,
              work_start='09:00', work_end='18:00', employment_type='full time',
              industry='software', outsourced=False, dispatched=False,
              evidence={'schedule': 'synthetic schedule'}, completeness='full_jd')
    storage.save_search([job.model_dump()], {'source': 'zhaopin'}, saved['generation'])
    assert Job.model_validate(storage.get('jobs', [])[0]) == job
    assert storage.get('jobs', [])[0]['posted_at'] == ''


def test_clear_generation_survives_reload_and_repeated_clear():
    initial = storage.snapshot()
    first = storage.clear()
    second = importlib.reload(storage).clear()
    assert second['generation'] == initial['generation'] + 2
    assert second['revision'] == first['revision'] + 1
    with pytest.raises(storage.ConflictError):
        storage.update_profile({'city': 'Late'}, generation=first['generation'])


@pytest.mark.parametrize('payload', [
    {'title': 'Synthetic', 'source': 'invented'},
    {'title': 'Synthetic', 'source_url': 'https://user:secret@example.invalid/job'},
    {'title': 'Synthetic', 'salary_min': 100, 'salary_max': 50},
])
def test_invalid_job_data_rejected(payload):
    with pytest.raises(ValidationError):
        Job.model_validate(payload)
