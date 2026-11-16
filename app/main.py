from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool
from starlette.background import BackgroundTask

from . import analysis_stream, llm, model_config, providers, search_tasks, storage
from .matching import analyze, infer_terms
from .models import ChatRequest, ImportRequest, Job, Profile, SearchRequest
from .profile import parse_profile
from .resume import MAX_BYTES, extract_resume
from .network import tls_context
from .build_info import code_build_id
from .frontend import render_index

VERSION = '1.0.0'
BUILD_ID = code_build_id()
STATIC = Path(__file__).resolve().parent / 'static'
_browser_launch_lock = threading.Lock()
_stream_lock = threading.Lock()
_stream_requests = {}


@asynccontextmanager
async def lifespan(_app):
    await run_in_threadpool(tls_context)
    yield
    search_tasks.cancel_all()


app = FastAPI(title='饭碗雷达 · 真实求职本地版', version=VERSION, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


@app.middleware('http')
async def local_mutations_only(request: Request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': '本地写入请求必须来自此应用页面'}, status_code=403)
    response = await call_next(request)
    response.headers.update({'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
                             'Referrer-Policy': 'no-referrer'})
    if request.url.path == '/' or request.url.path.startswith(('/api/', '/static/')):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(storage.ConflictError)
async def conflict_handler(_request, _exc):
    return JSONResponse({'detail': '画像或数据已被其他操作更新，请先重新载入；未覆盖较新的修改', 'code': 'revision_conflict'}, status_code=409)


@app.exception_handler(model_config.SettingsError)
async def settings_handler(_request, exc):
    return JSONResponse({'detail': str(exc), 'code': exc.code}, status_code=exc.status_code)


@app.exception_handler(llm.ModelError)
async def model_handler(_request, exc):
    return JSONResponse({'detail': str(exc), 'code': exc.code}, status_code=exc.status_code)


def _snapshot_dict(snapshot: dict) -> dict:
    result = dict(snapshot)
    if isinstance(result.get('profile'), Profile):
        result['profile'] = result['profile'].model_dump()
    return result


def _check_version(snapshot: dict, revision, generation):
    if revision is not None and revision != snapshot['revision']:
        raise HTTPException(409, '画像版本已变化，请重新载入后再操作')
    if generation is not None and generation != snapshot['generation']:
        raise HTTPException(409, '数据已经清除或重置，请刷新页面后再操作')


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def saved_jobs() -> list[Job]:
    rows = []
    for value in storage.get('jobs', []):
        try:
            job = Job.model_validate(value)
            if not job.is_demo and job.source != 'demo':
                rows.append(job)
        except (ValueError, TypeError):
            continue
    return rows


def report_jobs(jobs: list[Job], profile: Profile) -> dict:
    # Identical postings may carry several source links. Preserve differing
    # location, salary or full-JD text as separate offers rather than merging facts.
    groups = {}
    for job in jobs:
        if job.is_demo or job.source == 'demo':
            continue
        key = (re.sub(r'\s+', '', job.title).lower(), re.sub(r'\s+', '', job.company).lower(),
               job.city, job.salary_text, job.description, job.education, job.experience_min)
        if key in groups:
            groups[key]['source_links'].append({'source': job.source, 'url': job.source_url})
        else:
            groups[key] = job.model_dump() | {'analysis': analyze(job, profile),
                                             'source_links': [{'source': job.source, 'url': job.source_url}]}
    analyzed = list(groups.values())
    order = {'match': 0, 'needs_confirmation': 1, 'mismatch': 2}
    analyzed.sort(key=lambda j: (order.get(j['analysis']['verdict'], 1), -j['analysis']['score']))
    summary = {key: sum(j['analysis']['verdict'] == key for j in analyzed)
               for key in ('match', 'needs_confirmation', 'mismatch')}
    summary.update(total=len(analyzed), demo=0)
    return {'jobs': analyzed, 'summary': summary}


def _request_sources(request) -> list[str]:
    value = getattr(request, 'sources', None)
    source = getattr(request, 'source', None)
    return value or ([source] if source else ['boss'])


def _profile_fingerprint(profile: Profile) -> str:
    data = profile.model_dump(exclude={'name', 'resume_text', 'field_sources'})
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def make_plan(snapshot: dict, keyword: str, sources: list[str]) -> dict:
    profile = snapshot['profile']
    if set(profile.confirmation_required).intersection({'city', 'cities'}):
        raise HTTPException(422, '意向城市来自待确认的历史记录，请先核对画像并点击保存，再开始真实检索')
    if not profile.city.strip() and not profile.cities:
        raise HTTPException(422, '请先填写意向城市，再进行真实岗位检索')
    if not keyword.strip() and not profile.target_roles:
        raise HTTPException(422, '请先填写目标岗位或关键词，再进行有方向的检索')
    if any(source not in ('boss', 'zhaopin', 'liepin', 'ncss', 'public') for source in sources):
        raise HTTPException(422, '请选择已列出的真实来源；正常模式不接受虚构数据源')
    try:
        plan = providers.build_search_plan(profile, keyword, sources)
    except providers.SourceUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, '搜索条件无法转换，请核对城市、岗位和所选来源') from exc
    plan['profile_revision'] = snapshot['revision']
    plan['profile_fingerprint'] = _profile_fingerprint(profile)
    return plan


@app.get('/api/health')
def health():
    identity = hashlib.sha256(str(STATIC.parent.parent.resolve()).lower().encode()).hexdigest()[:12]
    return {'status': 'ok', 'version': VERSION, 'instance_id': identity, 'build_id': BUILD_ID,
            'llm_configured': model_config.public_settings()['configured'], 'mode': 'local_byok'}


@app.get('/api/profile')
def get_profile():
    return _snapshot_dict(storage.snapshot())


@app.post('/api/profile/interpret')
async def interpret_profile(payload: dict):
    from . import profile_intake
    try:
        request = profile_intake.InterpretRequest.model_validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, '请填写不超过两万字的要求，并使用当前页面版本重新整理') from exc
    before = storage.snapshot()
    _check_version(before, request.expected_revision, request.generation)
    try:
        result = await profile_intake.interpret(before, request.text, request.draft_patch)
    except model_config.SettingsError:
        raise
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, '当前条件草稿格式不正确，请检查数字、时间或刷新页面后重试') from exc
    after = storage.snapshot()
    if after['generation'] != before['generation'] or after['profile'] != before['profile']:
        raise storage.ConflictError('整理期间条件发生变化或数据已清除；原描述仍保留，请重新整理')
    # Canonical city aliases may be grouped into one visible item by the UI.
    # Avoid a misleading raw-field count in a human-facing preview summary.
    result['summary'] = ('已生成待确认草稿；请核对下方原话依据，确认填入后再保存。' if result.get('changes')
                         else '这段描述没有可填写的条件变更；未保存画像，请补充或查看待确认问题。')
    return result | {'revision': after['revision'], 'generation': after['generation'],
                     'mode': 'llm_profile_preview'}


@app.put('/api/profile')
@app.patch('/api/profile')
def update_profile(payload: dict):
    patch = payload.get('patch')
    if not isinstance(patch, dict):
        raise HTTPException(422, '保存接口已升级，请刷新页面后重试；需要提供变更字段 patch')
    try:
        result = storage.update_profile(patch, expected_revision=payload.get('expected_revision'),
                                        generation=payload.get('generation'), source='manual')
    except (ValueError, TypeError) as exc:
        if isinstance(exc, storage.ConflictError):
            raise
        raise HTTPException(422, '画像字段格式或范围不正确，请检查后重试') from exc
    return _snapshot_dict(result)


@app.get('/api/model-settings')
def get_model_settings():
    return model_config.public_settings()


@app.put('/api/model-settings')
def save_model_settings(payload: dict):
    return model_config.save_settings(payload)


@app.post('/api/model-settings/test')
async def test_model_settings():
    return await llm.test_connection()


@app.post('/api/model-settings/discover')
async def discover_model_settings(payload: dict):
    model_config.save_settings(payload)
    return await llm.discover_models()


@app.delete('/api/model-settings/key')
def remove_model_key():
    return model_config.remove_key()


@app.post('/api/chat', deprecated=True)
async def chat(request: ChatRequest):
    message = request.message.strip()
    if not message:
        raise HTTPException(422, '请输入你的情况或求职要求')
    cached = storage.cached_chat(request.request_id)
    if cached:
        return _snapshot_dict(storage.snapshot()) | {'reply': cached['reply'], 'mode': cached.get('mode', 'llm'), 'replayed': True, 'search_requested': False}
    before = storage.snapshot()
    _check_version(before, request.expected_revision, request.generation)
    jobs = report_jobs(saved_jobs(), before['profile'])['jobs']
    generated = await llm.generate_reply(before, message, jobs)
    # A credential accidentally pasted into chat is never written to history.
    safe_message = generated.get('safe_user_message', message)
    result = storage.commit_chat(generated['patch'], safe_message, generated['reply'],
                                 before['revision'], before['generation'], request.request_id, mode='llm')
    result = _snapshot_dict(result)
    result['search_requested'] = generated['search_requested']
    if generated['search_requested']:
        try:
            after = storage.snapshot()
            sources = storage.get('last_search_sources', ['boss'])
            plan = make_plan(after, '', sources)
            started = search_tasks.start(after['profile'], plan, after['generation'], sources)
            result.update(search_task=started, task_id=started['task_id'], search_plan=plan)
        except (HTTPException, RuntimeError, storage.ConflictError) as exc:
            result['search_warning'] = exc.detail if isinstance(exc, HTTPException) else '本轮聊天已保存，真实检索尚未启动；请查看当前任务或补充搜索条件后手动开始'
    return result


def _sse(event: str, value: dict) -> str:
    return 'event: ' + event + '\ndata: ' + json.dumps(value, ensure_ascii=False) + '\n\n'


@app.post('/api/chat/stream')
async def stream_analysis(request: ChatRequest, http_request: Request):
    message = request.message.strip()
    if not message:
        raise HTTPException(422, '请输入想分析的问题')
    before = storage.snapshot()
    cached = storage.cached_chat(request.request_id)
    if cached:
        async def replay():
            yield _sse('done', _snapshot_dict(storage.snapshot()) | {'reply': cached['reply'], 'mode': 'llm_stream', 'replayed': True})
        return StreamingResponse(replay(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache'})
    _check_version(before, request.expected_revision, request.generation)
    credentials = model_config.get_credentials()
    all_jobs = saved_jobs()
    by_id = {j.id: j for j in all_jobs}
    selected = request.job_ids
    if not selected and re.search(r'这个|这份|刚才|上一个|上一份|它', message):
        for item in reversed(before['messages']):
            refs = [i for i in item.get('job_ids', []) if i in by_id]
            if refs:
                selected = refs[:5]
                break
    if selected and any(jid not in by_id for jid in selected):
        raise HTTPException(404, '所选岗位已不在当前保存结果中，请重新选择后分析')
    chosen = [by_id[jid] for jid in selected] if selected else all_jobs[:5]
    context_jobs = report_jobs(chosen, before['profile'])['jobs']
    refs = [j['id'] for j in context_jobs]
    stream_key = (before['generation'], request.request_id)
    nonce = uuid.uuid4().hex
    with _stream_lock:
        stale = [k for k, (started, _) in _stream_requests.items() if time.monotonic() - started > 130]
        for key in stale:
            _stream_requests.pop(key, None)
        if stream_key in _stream_requests:
            raise HTTPException(409, '这轮流式分析仍在运行，请等待或停止当前生成后再重试')
        _stream_requests[stream_key] = (time.monotonic(), nonce)

    def release():
        with _stream_lock:
            if _stream_requests.get(stream_key, (None, None))[1] == nonce:
                _stream_requests.pop(stream_key, None)

    async def events():
        reply = ''
        last_check = time.monotonic()
        try:
            yield _sse('start', {'request_id': request.request_id, 'job_ids': refs, 'profile_revision': before['revision']})
            async for delta in analysis_stream.stream_reply(credentials, before, message, context_jobs):
                if await http_request.is_disconnected():
                    return
                if time.monotonic() - last_check > .8:
                    if storage.snapshot()['generation'] != before['generation']:
                        raise storage.ConflictError('Data cleared while streaming')
                    last_check = time.monotonic()
                reply += delta
                yield _sse('delta', {'text': delta})
            if not reply.strip():
                raise llm.ModelError('模型未返回有效分析，本轮未保存', 502, 'invalid_stream')
            if re.search(r'(?:我|我们|系统)(?:已|已经)(?:帮你|为你)?(?:成功)?(?:投递|发送简历|联系招聘|联系HR)', reply, re.I):
                raise llm.ModelError('模型误报了未执行的投递或联系操作，本轮未保存', 502, 'ungrounded_model_claim')
            if not context_jobs and re.search(r'(?:我|系统)(?:已|已经)(?:帮你|为你)?(?:找到|查到|筛选出)', reply):
                raise llm.ModelError('尚无实际岗位，模型误报查询结果，本轮未保存', 502, 'ungrounded_model_claim')
            if await http_request.is_disconnected():
                return
            safe_message = message.replace(credentials['api_key'], '[密钥已隐藏]')
            committed = storage.commit_chat({}, safe_message, reply, before['revision'], before['generation'],
                                             request.request_id, mode='llm_stream', context_job_ids=refs)
            yield _sse('done', _snapshot_dict(committed) | {'mode': 'llm_stream', 'job_ids': refs})
        except storage.ConflictError:
            yield _sse('error', {'detail': '画像或对话已更新，或数据已清除；本段基于旧上下文，未写入保存，请重新分析',
                                  'code': 'revision_conflict', 'http_status': 409})
        except llm.ModelError as exc:
            yield _sse('error', {'detail': str(exc), 'code': exc.code, 'http_status': exc.status_code})
        except asyncio.CancelledError:
            raise
        except Exception:
            yield _sse('error', {'detail': '流式分析中断，本段未保存，请重试；没有修改画像或执行投递',
                                  'code': 'stream_error', 'http_status': 502})
        finally:
            release()
    return StreamingResponse(events(), media_type='text/event-stream', background=BackgroundTask(release),
                             headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache'})


@app.post('/api/resume')
async def upload_resume(file: UploadFile = File(...), expected_revision: int | None = Form(default=None),
                        generation: int | None = Form(default=None)):
    before = storage.snapshot()
    _check_version(before, expected_revision, generation)
    filename = Path((file.filename or 'resume').replace('\\', '/')).name
    content = await file.read(MAX_BYTES + 1)
    await file.close()
    try:
        text, warnings = await run_in_threadpool(extract_resume, content, filename)
        updated, _ = parse_profile(text, before['profile'], resume=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422, '简历解析失败，请尝试可复制文字的 PDF 或 TXT') from exc
    patch = {key: value for key, value in updated.model_dump().items() if value != before['profile'].model_dump().get(key)}
    result = storage.update_profile(patch, expected_revision=before['revision'], generation=before['generation'], source='resume')
    return _snapshot_dict(result) | {'filename': filename, 'extracted_characters': len(text),
                                     'preview': text[:1800], 'warnings': warnings}


@app.get('/api/sources')
def sources():
    rows = providers.source_statuses()
    boss = next((s for s in rows if s.get('id') == 'boss'), {})
    return {'sources': rows, 'boss_available': boss.get('status') in ('connected', 'browser_connected', 'ready', 'available'),
            'llm_configured': model_config.public_settings()['configured']}


@app.post('/api/browser/open')
def open_recruiting_browser():
    if providers.boss_available():
        return {'status': 'connected', 'message': 'BOSS 专用浏览器已经运行。请切换到那个窗口本人登录；完成后刷新连接状态，首次真实查询再验证平台是否可读。'}
    if os.name != 'nt':
        raise HTTPException(422, '当前一键启动脚本适用于 Windows；请按说明手动启动本机专用 CDP 浏览器')
    if not _browser_launch_lock.acquire(blocking=False):
        raise HTTPException(409, '专用浏览器正在启动，请稍后刷新连接状态')
    try:
        script = Path(__file__).resolve().parents[1] / '打开BOSS登录.ps1'
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=18,
                                env={k: v for k, v in os.environ.items() if k != 'JOB_LLM_API_KEY'})
        if result.returncode:
            raise HTTPException(409, '浏览器未能启动，请检查 Chrome/Edge 安装情况或按说明手动启动；没有读取或复制登录数据')
        return {'status': 'opened', 'message': '已请求打开 BOSS 专用浏览器，请在该窗口本人完成登录或验证，然后刷新连接状态。'}
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, '浏览器启动等待超时，请检查是否已出现专用窗口后再刷新状态') from exc
    finally:
        _browser_launch_lock.release()


@app.post('/api/search/plan')
def preview_search(request: SearchRequest):
    before = storage.snapshot()
    return {'plan': make_plan(before, request.keyword, _request_sources(request))}


@app.post('/api/search', status_code=202)
def search(request: SearchRequest):
    before = storage.snapshot()
    _check_version(before, getattr(request, 'expected_revision', None), getattr(request, 'generation', None))
    selected = _request_sources(request)
    plan = make_plan(before, request.keyword, selected)
    try:
        result = search_tasks.start(before['profile'], plan, before['generation'], selected)
    except RuntimeError as exc:
        if isinstance(exc, storage.ConflictError):
            raise
        raise HTTPException(409, '已有检索进行中，请等完成或先取消') from exc
    storage.put('last_search_sources', selected)
    return result


@app.get('/api/tasks/{task_id}')
def task_status(task_id: str):
    task = search_tasks.get(task_id)
    if not task:
        raise HTTPException(404, '任务不存在或已清除，请刷新当前结果')
    snapshot = storage.snapshot()
    jobs = [Job.model_validate(j) for j in task.get('jobs', []) if not j.get('is_demo') and j.get('source') != 'demo']
    result = task | report_jobs(jobs, snapshot['profile'])
    plan = task.get('plan', {})
    changed = (plan.get('profile_fingerprint') != _profile_fingerprint(snapshot['profile'])
               if plan.get('profile_fingerprint') else plan.get('profile_revision') != snapshot['revision'])
    if changed:
        result['profile_changed'] = True
        result['warnings'] = list(result.get('warnings', [])) + ['画像已变化，本轮采集仍按开始时条件执行；当前匹配分析使用最新画像，需要更新范围请重新搜索']
    return result


@app.post('/api/tasks/{task_id}/cancel')
def cancel_task(task_id: str):
    if not search_tasks.cancel(task_id):
        raise HTTPException(404, '没有可取消的运行中任务')
    return {'status': 'cancelling', 'task_id': task_id}


@app.get('/api/jobs')
def list_jobs():
    return storage.get('search_meta', {}) | report_jobs(saved_jobs(), storage.profile())


@app.post('/api/jobs/import')
def import_jobs(request: ImportRequest):
    before = storage.snapshot()
    output = []
    for job in request.jobs:
        if job.is_demo or job.source == 'demo':
            raise HTTPException(422, '正常岗位列表不接受虚构数据；请导入实际招聘原文及来源链接')
        if not job.source_url or len(job.description.strip()) < 15:
            raise HTTPException(422, '请为每条实际岗位提供来源链接及可核对的招聘正文')
        job = job.model_copy(update={'source': 'import', 'completeness': 'user_provided'})
        if not job.id:
            job.id = 'import-' + hashlib.sha256((job.source_url + job.title + job.company).encode()).hexdigest()[:18]
        job.collected_at = timestamp()
        if not job.contacts:
            job.contacts = providers.public_contacts(job.description)
            for contact in job.contacts:
                contact.label = '用户导入原文中的联系方式，未独立核实'
        output.append(infer_terms(job))
    meta = {'source': 'import', 'collected_at': timestamp(), 'stale': False,
            'warnings': ['来自用户提供的招聘原文；未冒充平台实时采集或独立真实性核验。']}
    storage.save_search([j.model_dump() for j in output], meta, before['generation'])
    return meta | report_jobs(output, storage.profile())


def find_job(job_id: str):
    job = next((j for j in saved_jobs() if j.id == job_id), None)
    if not job:
        raise HTTPException(404, '岗位不存在，可能已被新一轮检索替换')
    return job


@app.get('/api/jobs/{job_id}')
def job_detail(job_id: str):
    job = find_job(job_id)
    return job.model_dump() | {'analysis': analyze(job, storage.profile())}


@app.post('/api/jobs/{job_id}/application')
def application(job_id: str):
    job = find_job(job_id)
    profile = storage.profile()
    analysis = analyze(job, profile)
    facts = []
    if profile.education:
        facts.append(profile.education + '学历')
    if profile.experience_years is not None:
        facts.append(f'{profile.experience_years:g}年工作经验')
    if profile.skills:
        facts.append('具备' + '、'.join(profile.skills[:5]) + '等技能')
    intro = '，'.join(facts)
    message = f'您好，我对贵司的「{job.title}」岗位感兴趣。' + (f'我目前的情况是：{intro}。' if intro else '')
    questions = analysis['questions'][:5] or ['请问岗位目前是否仍在招聘？方便确认实际工作地点、薪资口径和休息制度吗？']
    message += '\n想先确认：\n' + '\n'.join(f'{i+1}. {q}' for i, q in enumerate(questions))
    return {'message': message, 'questions': questions, 'source_url': job.source_url,
            'contacts': [c.model_dump() for c in job.contacts],
            'notice': '仅生成草稿，未发送消息或简历；联系方式仅来自原文明确内容，请本人核实后使用。'}


@app.delete('/api/data')
def delete_data(x_confirm_delete: str = Header(default='')):
    if x_confirm_delete != 'yes':
        raise HTTPException(400, '请先确认清除本机画像、对话和岗位记录；模型密钥需在设置中另行移除')
    storage.clear()
    search_tasks.cancel_all()
    return {'status': 'cleared'}


@app.get('/')
def index():
    return HTMLResponse(render_index((STATIC / 'index.html').read_text(encoding='utf-8'), BUILD_ID))


app.mount('/static', StaticFiles(directory=STATIC, check_dir=False), name='static')
