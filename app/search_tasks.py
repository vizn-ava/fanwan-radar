"""Bounded background collection with durable progress and generation fencing."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading
import uuid

from . import storage
from .matching import infer_terms
from .models import Job, Profile
from . import providers

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='recruiting')
_lock = threading.RLock()
_tasks: dict[str, dict] = {}
_events: dict[str, threading.Event] = {}
_ACTIVE = {'queued', 'running'}
_ERRORS = {'unauthenticated', 'restricted', 'capture_timeout', 'cdp_error', 'schema_error', 'unavailable', 'failed'}


def now():
    return datetime.now(timezone.utc).isoformat()


def _safe_job(value) -> Job | None:
    try:
        job = value if isinstance(value, Job) else Job.model_validate(value)
        if job.is_demo or job.source == 'demo':
            return None
        return infer_terms(job)
    except (ValueError, TypeError):
        return None


def _alive(task: dict) -> bool:
    return storage.snapshot()['generation'] == task['generation']


def _public(task: dict) -> dict:
    return {k: v for k, v in task.items() if k not in ('profile', 'generation', 'previous_jobs', 'previous_meta')}


def _persist(task: dict):
    current = task['jobs']
    old_meta = task['previous_meta']
    meta = {k: v for k, v in _public(task).items() if k not in ('jobs', 'summary')}
    meta.update(source='multi', stale=not bool(current), last_attempt_at=task['started_at'],
                collected_at=task['started_at'] if current else old_meta.get('collected_at', ''))
    storage.save_search(current or task['previous_jobs'], meta, task['generation'])


def _source_ids(plan: dict, requested: list[str]) -> list[str]:
    values = plan.get('sources')
    if isinstance(values, list):
        found = [x if isinstance(x, str) else x.get('id', x.get('source', '')) for x in values if isinstance(x, (str, dict))]
        return list(dict.fromkeys(x for x in found if x)) or requested
    return requested


def _queries(plan: dict, source: str) -> list[dict]:
    for entry in plan.get('sources', []):
        if isinstance(entry, dict) and entry.get('id', entry.get('source')) == source:
            found = entry.get('queries', [])
            if isinstance(found, list) and found:
                # The adapter owns its cross-query time/request budget. Pass the
                # exact visible source plan instead of silently truncating it.
                return [entry]
    value = plan.get('queries', [])
    if isinstance(value, dict):
        value = value.get(source, [])
    if isinstance(value, list):
        found = [q for q in value if isinstance(q, dict) and q.get('source', q.get('platform', source)) == source]
        if found:
            return found
    for entry in plan.get('sources', []):
        if isinstance(entry, dict) and entry.get('id', entry.get('source')) == source:
            found = entry.get('queries', [])
            if isinstance(found, list) and found:
                return [q for q in found if isinstance(q, dict)]
    # A source may interpret the complete structured per-platform plan itself.
    return [{'source': source, 'keyword': plan.get('keyword', ''), 'plan': plan}]


def start(profile: Profile, plan: dict, generation: int, requested: list[str]) -> dict:
    with _lock:
        if any(t['status'] in _ACTIVE and t['generation'] == generation for t in _tasks.values()):
            raise RuntimeError('已有真实检索任务进行中，请等待完成或先取消')
        task_id = uuid.uuid4().hex
        previous = [j for j in storage.get('jobs', []) if isinstance(j, dict) and not j.get('is_demo') and j.get('source') != 'demo']
        ids = _source_ids(plan, requested)
        task = {'task_id': task_id, 'status': 'queued', 'stage': 'queued', 'progress': 0,
                'plan': plan, 'sources': [{'id': s, 'status': 'queued', 'stage': 'queued', 'count': 0} for s in ids],
                'jobs': [], 'warnings': [], 'diagnostics': {}, 'started_at': now(), 'finished_at': None,
                'generation': generation, 'profile': profile.model_dump(),
                'previous_jobs': previous, 'previous_meta': storage.get('search_meta', {})}
        _persist(task)
        _tasks[task_id] = task
        _events[task_id] = threading.Event()
        _executor.submit(_run, task_id)
        return {'task_id': task_id, 'plan': plan}


def _run(task_id: str):
    with _lock:
        task = _tasks.get(task_id)
        event = _events.get(task_id)
    if task is None or event is None:
        return
    profile = Profile.model_validate(task['profile'])

    def cancelled():
        return event.is_set() or not _alive(task)

    def ingest(values):
        for value in values:
            job = _safe_job(value)
            if not job:
                continue
            job.collected_at = job.collected_at or now()
            rows = {j.get('id') or j.get('source_url'): j for j in task['jobs']}
            rows[job.id or job.source_url] = job.model_dump()
            task['jobs'] = list(rows.values())

    try:
        task['status'], task['stage'] = 'running', 'preflight'
        _persist(task)
        total = max(len(task['sources']), 1)
        for index, entry in enumerate(task['sources']):
            if cancelled():
                break
            source = entry['id']
            entry.update(status='running', stage='preflight')
            task['stage'] = source + ':preflight'
            queries = _queries(task['plan'], source)
            statuses = []
            for q_index, query in enumerate(queries[:3]):
                if cancelled():
                    break

                def emit(update: dict):
                    if cancelled() or not isinstance(update, dict):
                        return
                    with _lock:
                        values = update.get('jobs', [])
                        if update.get('job'):
                            values = list(values) + [update['job']]
                        if isinstance(values, list):
                            ingest(values)
                        stage = str(update.get('stage', entry['stage']))[:100]
                        count = sum(j.get('source') == source for j in task['jobs'])
                        full = sum(j.get('source') == source and j.get('completeness') == 'full_jd' for j in task['jobs'])
                        entry.update(stage=stage, count=count, jobs_count=count, full_jd_count=full, list_only_count=count-full)
                        task['stage'] = source + ':' + stage
                        local = update.get('progress', 0)
                        local = float(local) if isinstance(local, (int, float)) else 0
                        task['progress'] = min(97, round(100 * (index + (q_index + min(max(local, 0), 100) / 100) / max(len(queries[:3]), 1)) / total))
                        _persist(task)

                try:
                    result = providers.collect_source(source, profile, query, emit, cancelled)
                    if not isinstance(result, dict):
                        raise ValueError('invalid source result')
                    ingest(result.get('jobs', []))
                    status = result.get('status', 'schema_error')
                    statuses.append(status)
                    task['warnings'].extend(str(w)[:1000] for w in result.get('warnings', []) if isinstance(w, str))
                    task['diagnostics'][f'{source}:{q_index}'] = result.get('diagnostics', {})
                    entry['warnings'] = list(result.get('warnings', []))
                    if status in ('restricted', 'unauthenticated', 'cdp_error', 'unavailable'):
                        # Do not continue querying a platform that requires manual intervention.
                        break
                except providers.SourceUnavailable as exc:
                    statuses.append(getattr(exc, 'status', 'unavailable'))
                    task['warnings'].append(str(exc)[:1000])
                    task['diagnostics'][f'{source}:{q_index}'] = getattr(exc, 'diagnostics', {})
                    break
                except (ValueError, TypeError, OSError):
                    statuses.append('schema_error')
                    task['warnings'].append(f'{source} 返回的数据未通过校验；已保留其他来源取得的真实结果')
                    break
            count = sum(j.get('source') == source for j in task['jobs'])
            if cancelled():
                entry['status'] = 'cancelled'
            elif any(s in _ERRORS for s in statuses):
                entry['status'] = 'partial' if count else next(s for s in statuses if s in _ERRORS)
            elif 'partial' in statuses:
                entry['status'] = 'partial'
            else:
                entry['status'] = 'success' if count else 'empty'
            entry.update(stage='finished', count=count)
            task['progress'] = min(97, round(100 * (index + 1) / total))
            _persist(task)
        if cancelled():
            task['status'] = 'cancelled'
            task['warnings'].append('检索已停止；已经取得的真实记录保留，未用模拟数据补足')
        elif task['jobs']:
            task['status'] = 'partial' if any(e['status'] not in ('success', 'empty') for e in task['sources']) else 'success'
        else:
            task['status'] = 'failed' if any(e['status'] in _ERRORS for e in task['sources']) else 'empty'
        task.update(stage='finished', progress=100, finished_at=now())
        task['warnings'] = list(dict.fromkeys(task['warnings']))
        if _alive(task):
            _persist(task)
    except storage.ConflictError:
        task.update(status='cancelled', stage='cleared', finished_at=now())
    except Exception:
        # Preserve safe status rather than letting a worker disappear as "running".
        task.update(status='failed', stage='internal_error', finished_at=now())
        task['warnings'].append('检索任务异常停止；未将失败记录为零岗位，请检查脱敏诊断后重试')
        if _alive(task):
            try:
                _persist(task)
            except storage.ConflictError:
                pass


def get(task_id: str) -> dict | None:
    with _lock:
        task = _tasks.get(task_id)
        if task and _alive(task):
            return _public(task)
    meta = storage.get('search_meta', {})
    if meta.get('task_id') != task_id:
        return None
    result = dict(meta)
    result['jobs'] = [] if result.get('stale') else [j for j in storage.get('jobs', []) if not j.get('is_demo') and j.get('source') != 'demo']
    if result.get('status') in _ACTIVE:
        result.update(status='failed', stage='interrupted', warnings=list(result.get('warnings', [])) + ['服务已重启，本轮任务中断；已保存的真实部分结果仍可查看'], finished_at=now())
    return result


def cancel(task_id: str) -> bool:
    with _lock:
        event = _events.get(task_id)
        if not event:
            return False
        event.set()
        return True


def cancel_all():
    with _lock:
        for event in _events.values():
            event.set()
        _tasks.clear()
        _events.clear()
