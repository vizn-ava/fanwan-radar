from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .models import Job, Profile

ROOT = Path(__file__).resolve().parents[1]


class ConflictError(ValueError):
    """The caller's revision or deletion generation is no longer current."""


@contextmanager
def connection():
    directory = Path(os.getenv('JOB_DEMO_DATA_DIR', str(ROOT / 'data')))
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / 'local.sqlite3', timeout=15)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _get(db, key, default):
    row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _put(db, key, value):
    db.execute('INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               (key, json.dumps(value, ensure_ascii=False, allow_nan=False)))


def get(key, default):
    with connection() as db:
        return _get(db, key, default)


def put(key, value):
    with connection() as db:
        _put(db, key, value)


# Each group corresponds to one matching priority, with unknown legacy values
# excluded until the user confirms them.
CONDITIONS = {
    'location': ('city', 'cities', 'districts', 'allow_relocation'),
    'salary': ('salary_min', 'salary_max'),
    'insurance': ('insurance_requirement', 'insurance_during_probation', 'fund_base_min'),
    'schedule': ('accepted_schedules',), 'hours': ('max_daily_hours',),
    'night_shift': ('accept_night_shift',), 'rotating_shift': ('accept_rotating_shift',),
    'overtime': ('accept_overtime',), 'work_time': ('work_start', 'work_end'),
    'employment': ('employment_types',), 'industry': ('industries',),
    'excluded_terms': ('excluded_terms',), 'outsourcing': ('accept_outsourcing',),
    'dispatch': ('accept_dispatch',), 'remote': ('allow_remote',),
    'commute': ('max_commute_minutes',), 'role': ('target_roles',),
    'education': ('education',), 'experience': ('experience_years',),
}
METADATA = '_v2_metadata'
CHAT_PREFIX = '_v2_chat:'
PROTECTED = {'field_sources', 'confirmation_required'}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _group_active(data, fields):
    pending = data.get('confirmation_required', [])
    return any(field not in pending and data.get(field) is not None
               and (isinstance(data.get(field), bool)
                    or data.get(field) not in ('', 'any', [], 0)
                    or field == 'experience_years') for field in fields)


def _priorities(data):
    priorities = data.setdefault('priorities', {})
    for group, fields in CONDITIONS.items():
        active = _group_active(data, fields)
        if not active:
            priorities[group] = 'ignore'
        elif group not in priorities:
            priorities[group] = 'must'


def _migrate(raw):
    data = dict(raw)
    sources = dict(data.get('field_sources', {}))
    pending = list(data.get('confirmation_required', []))
    old_defaults = {'city': '\u90d1\u5dde', 'salary_min': 6000, 'salary_basis': 'base',
                    'require_double_rest': True, 'require_housing_fund': True,
                    'allow_remote': False}
    for field, default in old_defaults.items():
        if field in data and data[field] == default and field not in sources:
            pending.append(field)
    if 'accepted_schedules' not in data and data.get('require_double_rest') is True:
        data['accepted_schedules'] = [{'kind': 'weekly_double'}]
        sources['accepted_schedules'] = sources.get('require_double_rest', 'legacy')
        if 'require_double_rest' in pending:
            pending.append('accepted_schedules')
    if 'insurance_requirement' not in data and data.get('require_housing_fund') is True:
        data['insurance_requirement'] = 'five_and_fund'
        sources['insurance_requirement'] = sources.get('require_housing_fund', 'legacy')
        if 'require_housing_fund' in pending:
            pending.append('insurance_requirement')
    for field in raw:
        if field not in PROTECTED and field != 'priorities':
            sources.setdefault(field, 'legacy')
    data['field_sources'] = sources
    data['confirmation_required'] = list(dict.fromkeys(pending))
    _priorities(data)
    return Profile.model_validate(data)


def _state(db):
    meta = _get(db, METADATA, None)
    if meta is None:
        raw = _get(db, 'profile', {})
        p = _migrate(raw) if raw else Profile()
        meta = {'revision': 0, 'generation': 0, 'updated_at': _now()}
        _put(db, 'profile', p.model_dump())
        _put(db, METADATA, meta)
    else:
        p = Profile.model_validate(_get(db, 'profile', {}))
    return {'profile': p, **meta, 'messages': _get(db, 'messages', [])}


def snapshot():
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        return _state(db)


def profile():
    return snapshot()['profile']


def _check(state, expected_revision=None, generation=None):
    if generation is not None and generation != state['generation']:
        raise ConflictError('Data was cleared; reload before saving')
    if expected_revision is not None and expected_revision != state['revision']:
        raise ConflictError('Profile changed; reload before saving')


def _apply(state, patch, source):
    if not isinstance(patch, dict):
        raise ValueError('Profile patch must be an object')
    allowed = set(Profile.model_fields) - PROTECTED
    if set(patch) - allowed:
        raise ValueError('Unknown or protected profile patch fields')
    if source not in ('manual', 'resume', 'chat', 'llm'):
        raise ValueError('Unknown profile source')
    if 'resume_text' in patch and source != 'resume':
        raise ValueError('Only resume updates may replace resume_text')
    data = state['profile'].model_dump()
    data.update(patch)
    Profile.model_validate(data, strict=True)
    data['priorities'] = state['profile'].priorities | patch.get('priorities', {})
    # Explicit legacy toggles remain accepted, but update the authoritative fields.
    linked = {}
    if 'require_double_rest' in patch and 'accepted_schedules' not in patch:
        linked['accepted_schedules'] = [{'kind': 'weekly_double'}] if patch['require_double_rest'] else []
    if 'require_housing_fund' in patch and 'insurance_requirement' not in patch:
        linked['insurance_requirement'] = 'five_and_fund' if patch['require_housing_fund'] else 'any'
    data.update(linked)
    changed = set(patch) | set(linked)
    for field in changed - {'priorities'}:
        data['field_sources'][field] = source
    data['confirmation_required'] = [f for f in data['confirmation_required'] if f not in changed]
    if 'accepted_schedules' in changed:
        data['confirmation_required'] = [f for f in data['confirmation_required'] if f != 'require_double_rest']
    if 'insurance_requirement' in changed:
        data['confirmation_required'] = [f for f in data['confirmation_required'] if f != 'require_housing_fund']
    # Activate a previously empty group, but do not silently turn an existing
    # preference/ignored condition into a hard requirement when its value changes.
    for group, fields in CONDITIONS.items():
        if (changed.intersection(fields) and group not in patch.get('priorities', {})
                and not _group_active(state['profile'].model_dump(), fields)):
            data['priorities'].pop(group, None)
    _priorities(data)
    return Profile.model_validate(data)


def _save_profile(db, state, profile):
    state = {**state, 'profile': profile, 'revision': state['revision'] + 1, 'updated_at': _now()}
    _put(db, 'profile', profile.model_dump())
    _put(db, METADATA, {key: state[key] for key in ('revision', 'generation', 'updated_at')})
    return state


def update_profile(patch: dict, expected_revision: int | None = None,
                   generation: int | None = None, source='manual'):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        state = _state(db)
        _check(state, expected_revision, generation)
        return _save_profile(db, state, _apply(state, patch, source))


def _cached(db, request_id):
    cached = _get(db, CHAT_PREFIX + request_id, None)
    if cached is not None:
        cached['profile'] = Profile.model_validate(cached['profile'])
    return cached


def cached_chat(request_id: str):
    with connection() as db:
        return _cached(db, request_id)


def commit_chat(patch: dict, user_content: str, reply: str, expected_revision: int,
                generation: int, request_id: str, mode='llm', context_job_ids=None):
    if not isinstance(patch, dict):
        raise ValueError('Profile patch must be an object')
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
        raise ValueError('Invalid chat request id')
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        state = _state(db)
        _check(state, generation=generation)
        cached = _cached(db, request_id)
        if cached is not None:
            return cached
        _check(state, expected_revision)
        next_profile = _apply(state, patch, 'chat') if patch else state['profile']
        state = _save_profile(db, state, next_profile)
        refs = list(dict.fromkeys(context_job_ids or []))[:5]
        state['messages'] = state['messages'][-38:] + [
            {'role': 'user', 'content': user_content, 'job_ids': refs},
            {'role': 'assistant', 'content': reply, 'mode': mode, 'job_ids': refs},
        ]
        _put(db, 'messages', state['messages'])
        result = {**state, 'reply': reply, 'mode': mode}
        _put(db, CHAT_PREFIX + request_id, {**result, 'profile': result['profile'].model_dump()})
        # Each replay entry contains a profile snapshot. Keep a bounded window
        # instead of retaining an ever-growing copy for every historical turn.
        db.execute("DELETE FROM state WHERE key IN (SELECT key FROM state WHERE substr(key,1,9)='_v2_chat:' ORDER BY rowid DESC LIMIT -1 OFFSET 80)")
        return result


def save_search(jobs: list[dict], meta: dict, generation: int):
    validated = [Job.model_validate(job).model_dump() for job in jobs]
    if not isinstance(meta, dict):
        raise ValueError('Search metadata must be an object')
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        state = _state(db)
        _check(state, generation=generation)
        _put(db, 'jobs', validated)
        _put(db, 'search_meta', meta)


def clear():
    with connection() as db:
        db.execute('PRAGMA secure_delete=ON')
        db.execute('BEGIN IMMEDIATE')
        state = _state(db)
        db.execute("DELETE FROM state WHERE key IN ('profile','messages','jobs','search_meta') OR substr(key,1,9)='_v2_chat:'")
        state['generation'] += 1
        result = _save_profile(db, state, Profile())
        result['messages'] = []
        return result
