"""Polite page navigation, passive API capture, incremental validated records.

Reuses map_api_job / extract_detail_fields / EXTRACT_DETAIL_JS from the unmodified
third_party/boss-zhipin-scraper (MIT, Copyright (c) 2026 eatmoreduck).
The original license remains at third_party/boss-zhipin-scraper/LICENSE.
No upstream CLI, login probe, visibility overrides, or injected API calls run.
"""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlencode, urlsplit

import websocket
from .providers import SourceUnavailable, cdp_preflight, normalize_boss

UPSTREAM = Path(__file__).resolve().parents[1] / 'third_party' / 'boss-zhipin-scraper' / 'scripts' / 'boss_cdp_raw.py'


def upstream_components():
    name = '_fanwan_boss_mit'
    if name not in sys.modules:
        if not UPSTREAM.is_file():
            raise SourceUnavailable('缺少 MIT 上游字段适配组件。')
        spec = importlib.util.spec_from_file_location(name, UPSTREAM)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name, None)
            raise SourceUnavailable('上游适配组件加载失败。', 'schema_error') from None
    return sys.modules[name]


class PassiveCDP:
    def __init__(self, cancelled):
        self.cancelled = cancelled
        data = cdp_preflight()
        self.ws = websocket.create_connection(data['webSocketDebuggerUrl'], timeout=0.5,
                                               suppress_origin=True, http_proxy_host=None)
        self.sequence = 0
        self.responses = {}
        self.finished = set()
        self.target = None
        self.sid = None

    def check(self):
        if self.cancelled():
            raise SourceUnavailable('采集已取消，已收到的真实记录保留。', 'cancelled', 'cancel')

    def receive(self):
        self.check()
        try:
            message = json.loads(self.ws.recv())
        except websocket.WebSocketTimeoutException:
            return {}
        if message.get('sessionId') == self.sid:
            params = message.get('params', {})
            if message.get('method') == 'Network.responseReceived':
                response = params.get('response', {})
                url = urlsplit(response.get('url', ''))
                # Retain only job-list request IDs and status, never headers/cookies.
                if url.hostname == 'www.zhipin.com' and url.path == '/wapi/zpgeek/search/joblist.json':
                    self.responses[params['requestId']] = response.get('status', 0)
            elif message.get('method') == 'Network.loadingFinished':
                self.finished.add(params.get('requestId'))
        return message

    def send(self, method, params=None, sid=None, timeout=4, cleanup=False):
        self.sequence += 1
        ident = self.sequence
        message = {'id': ident, 'method': method, 'params': params or {}}
        if sid:
            message['sessionId'] = sid
        self.ws.send(json.dumps(message))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cleanup:
                try:
                    reply = json.loads(self.ws.recv())
                except websocket.WebSocketTimeoutException:
                    continue
            else:
                reply = self.receive()
            if reply.get('id') == ident:
                if 'error' in reply:
                    raise SourceUnavailable('CDP 命令失败。', 'cdp_error', 'browser', {'method': method})
                return reply.get('result', {})
        raise SourceUnavailable('浏览器命令超时。', 'capture_timeout', 'browser', {'method': method})

    def open(self):
        self.target = self.send('Target.createTarget', {'url': 'about:blank', 'background': True})['targetId']
        self.sid = self.send('Target.attachToTarget', {'targetId': self.target, 'flatten': True})['sessionId']
        self.send('Network.enable', {}, self.sid)
        self.send('Page.enable', {}, self.sid)

    def evaluate(self, expression):
        return self.send('Runtime.evaluate', {'expression': expression, 'returnByValue': True}, self.sid).get('result', {}).get('value')

    def guard(self):
        text = self.evaluate("JSON.stringify({url:location.href,title:document.title,text:document.body?document.body.innerText.slice(0,16000):''})")
        try:
            page = json.loads(text or '{}')
        except (TypeError, ValueError):
            return
        gate = classify_page(page)
        if gate:
            raise SourceUnavailable('平台要求本人完成登录或安全验证，已停止此平台。', gate, 'platform_gate')

    def list_response(self, url, timeout=12):
        self.responses.clear()
        self.finished.clear()
        self.send('Page.navigate', {'url': url}, self.sid)
        deadline = time.monotonic() + timeout
        checked = 0
        while time.monotonic() < deadline:
            self.check()
            for rid in list(self.responses):
                if rid not in self.finished:
                    continue
                status = self.responses.pop(rid)
                if status in (401, 403, 429):
                    raise SourceUnavailable('平台拒绝访问；停止采集。', 'restricted', 'list', {'http_status': status})
                body = self.send('Network.getResponseBody', {'requestId': rid}, self.sid)
                content = body.get('body', '')
                if body.get('base64Encoded'):
                    content = base64.b64decode(content).decode('utf-8')
                if len(content) > 4_000_000:
                    raise SourceUnavailable('平台返回结构超出安全上限。', 'schema_error', 'list')
                try:
                    payload = json.loads(content)
                except ValueError:
                    raise SourceUnavailable('列表不是有效 JSON。', 'schema_error', 'list') from None
                self.guard()
                return validate_list(payload)
            if time.monotonic() - checked > 1.5:
                self.guard()
                checked = time.monotonic()
            self.receive()
        self.guard()
        raise SourceUnavailable('页面未返回可解析的岗位列表。', 'capture_timeout', 'list')

    def detail(self, url, upstream, timeout=8):
        self.send('Page.navigate', {'url': url}, self.sid)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.guard()
            raw = self.evaluate(upstream.EXTRACT_DETAIL_JS)
            try:
                extracted = json.loads(raw or '{}')
                validated = upstream.extract_detail_fields(extracted)
                validated['tags'] = extracted.get('tags', [])
                return validated
            except upstream.DetailLoginRequiredError:
                raise SourceUnavailable('详情需要本人登录，已停止此平台。', 'unauthenticated', 'detail') from None
            except (ValueError, TypeError):
                pass
            pause(self.cancelled, 0.6)
        raise SourceUnavailable('完整 JD 未在预算内呈现，保留列表记录。', 'capture_timeout', 'detail')

    def close(self):
        try:
            if self.target:
                self.send('Target.closeTarget', {'targetId': self.target}, timeout=1, cleanup=True)
        finally:
            self.ws.close()


def classify_page(page):
    url, text = str(page.get('url', '')).lower(), str(page.get('text', ''))
    title = str(page.get('title', '')).lower()
    if any(x in title for x in ('验证码', '安全中心', 'security verification')) or any(x in url for x in ('/captcha', '/verify', '/safe/')) or any(x in text for x in ('安全验证', '访问异常', '请完成验证', '拖动滑块', '操作过于频繁', '请通过验证')):
        return 'restricted'
    if '/web/user/' in url or '登录后查看' in text or '登录后可查看' in text:
        return 'unauthenticated'
    return None


def validate_list(payload):
    if not isinstance(payload, dict):
        raise SourceUnavailable('列表结构无效。', 'schema_error', 'list')
    if 'code' not in payload:
        raise SourceUnavailable('列表缺少响应状态。', 'schema_error', 'list')
    code = payload.get('code')
    if code not in (0, '0'):
        status = 'unauthenticated' if str(code) in ('7', '10') else 'restricted'
        raise SourceUnavailable('平台返回访问限制，已停止此平台。', status, 'list',
                                {'platform_code': code if isinstance(code, (int, str)) and len(str(code)) < 16 else 'unknown'})
    data = payload.get('zpData')
    if not isinstance(data, dict) or not isinstance(data.get('jobList'), list):
        raise SourceUnavailable('平台列表字段变化，未当作零岗位。', 'schema_error', 'list')
    return data['jobList']


def pause(cancelled, seconds=1.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancelled():
            raise SourceUnavailable('采集已取消。', 'cancelled', 'cancel')
        time.sleep(min(.1, max(0, end - time.monotonic())))


def city_code(city, upstream):
    if not city or city == '全国':
        return '100010000'
    if city.isdigit() and len(city) == 9:
        return city
    mapping = upstream.load_local_city_map()
    # Upstream returns code maps as a tuple in some versions.
    if isinstance(mapping, tuple):
        mapping = mapping[0]
    if isinstance(mapping, dict):
        for name, code in mapping.items():
            if str(name).removesuffix('市') == city.removesuffix('市') and str(code).isdigit():
                return str(code)
    raise SourceUnavailable('本地城市代码未匹配，请选择受支持城市；不会回退全国搜索。', 'unavailable', 'plan')


def collect_source(source, profile, query, emit, cancelled):
    jobs, warnings, diagnostics = {}, [], {'source': source, 'queries_completed': 0, 'list_records': 0, 'full_jd': 0, 'invalid_records': 0}
    if source != 'boss':
        return {'jobs': [], 'status': 'unavailable', 'warnings': ['此来源尚未通过真实列表及完整 JD 验证。'], 'diagnostics': diagnostics}
    cdp = None
    status = 'empty'
    try:
        if cancelled():
            raise SourceUnavailable('采集已取消。', 'cancelled', 'cancel')
        upstream = upstream_components()
        queries = query.get('queries', [query])
        if not isinstance(queries, list) or not queries:
            raise SourceUnavailable('搜索计划没有查询。', 'unavailable', 'plan')
        emit({'stage': 'preflight', 'progress': 0, 'source': source})
        cdp = PassiveCDP(cancelled)
        cdp.open()
        # Total budget bounds multi-query work as well as individual page waits.
        deadline = time.monotonic() + 65
        for index, q in enumerate(queries[:6]):
            if time.monotonic() >= deadline:
                raise SourceUnavailable('本轮已达到时间预算，保留已收到的岗位。', 'capture_timeout', 'budget')
            cdp.check()
            params = {'query': str(q.get('keyword', ''))[:100], 'city': city_code(str(q.get('city', '')), upstream), 'page': 1}
            salary = q.get('platform_filters', {}).get('salary', '')
            if salary and all(s in ('402', '403', '404', '405', '406', '407') for s in salary.split(',')):
                params['salary'] = salary
            emit({'stage': 'list', 'source': source, 'query_index': index, 'progress': round(index / len(queries) * 100)})
            rows = cdp.list_response('https://www.zhipin.com/web/geek/job?' + urlencode(params))
            candidates = []
            for raw in rows[:min(20, max(1, int(q.get('max_list', 20))))]:
                try:
                    mapped = upstream.map_api_job(raw)
                    if not mapped:
                        raise ValueError
                    # Preserve API degree/experience explicitly, independent of tags.
                    mapped.update({k: raw.get(k) for k in ('jobDegree', 'jobExperience')})
                    job = normalize_boss(mapped, {})
                    if job.id not in jobs:
                        jobs[job.id] = job
                        candidates.append((mapped, job))
                        diagnostics['list_records'] += 1
                        emit({'stage': 'list', 'source': source, 'jobs': [job.model_dump()], 'operation': 'upsert'})
                except (ValueError, TypeError, SourceUnavailable):
                    diagnostics['invalid_records'] += 1
            diagnostics['queries_completed'] += 1
            if rows and not candidates and not jobs:
                raise SourceUnavailable('收到列表，但岗位字段均未通过校验。', 'schema_error', 'normalize')
            for mapped, job in candidates[:min(3, max(1, int(q.get('max_details', 2))))]:
                if time.monotonic() >= deadline:
                    raise SourceUnavailable('详情预算结束。', 'capture_timeout', 'budget')
                pause(cancelled)
                cdp.check()
                emit({'stage': 'detail', 'source': source, 'job_id': job.id, 'full_jd': diagnostics['full_jd']})
                try:
                    detail = cdp.detail(job.source_url, upstream)
                except SourceUnavailable as exc:
                    if exc.status != 'capture_timeout':
                        raise
                    warnings.append(str(exc))
                    continue
                full = normalize_boss(mapped, detail)
                jobs[full.id] = full
                diagnostics['full_jd'] += 1
                emit({'stage': 'detail', 'source': source, 'jobs': [full.model_dump()], 'operation': 'upsert', 'full_jd': diagnostics['full_jd']})
            if index + 1 < len(queries[:6]):
                pause(cancelled)
        if diagnostics['invalid_records']:
            warnings.append('部分列表记录字段无效，已跳过；未编造来源链接。')
        status = 'partial' if warnings or (jobs and diagnostics['full_jd'] < len(jobs)) else 'success' if jobs else 'empty'
    except SourceUnavailable as exc:
        status = exc.status
        diagnostics.update(exc.diagnostics)
        diagnostics['failure_status'] = exc.status
        diagnostics['stage'] = exc.stage
        warnings.append(str(exc))
        if jobs and status != 'cancelled':
            status = 'partial'
    except (websocket.WebSocketException, OSError, KeyError, ValueError, TypeError):
        status = 'partial' if jobs else 'cdp_error'
        diagnostics['failure_status'] = 'cdp_error'
        warnings.append('浏览器通信或响应结构异常，已保留收到的岗位。')
    finally:
        if cdp:
            try:
                cdp.close()
            except Exception:
                pass
    result = {'jobs': list(jobs.values()), 'status': status, 'warnings': list(dict.fromkeys(warnings)), 'diagnostics': diagnostics}
    emit({'stage': 'finished', 'source': source, 'status': status, 'progress': 100, 'diagnostics': diagnostics})
    return result
