"""Read-only, bounded queries of NCSS's publicly displayed recruitment data."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time

import httpx
from .models import Job
from .matching import infer_terms
from .network import tls_context

BASE = 'https://www.ncss.cn'
LIST_URL = BASE + '/student/jobs/jobslist/ajax/'


@lru_cache(maxsize=1)
def regions():
    return json.loads((Path(__file__).with_name('ncss_regions.json')).read_text(encoding='utf-8'))['regions']


def region_code(city: str) -> str:
    if city.strip() in ('全国', '不限'):
        return ''
    names = regions()
    for name in (city.strip(), city.strip() + '市', city.strip() + '省', city.strip().removesuffix('市')):
        if name in names:
            return names[name]
    raise ValueError('该来源未找到对应地区代码，请选择明确的城市或省份')


class DetailParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = {'description': [], 'location': [], 'company': [], 'title': []}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        parent_target = self.stack[-1][1] if self.stack else None
        skip = (self.stack[-1][2] if self.stack else False) or tag in ('script', 'style', 'noscript')
        target = parent_target
        classes = attrs.get('class', '').split()
        if 'mainContent' in classes:
            target = 'description'
        elif 'site-tag' in classes:
            target = 'location'
        elif attrs.get('id') == 'realCorpName':
            target = 'company'
        elif tag == 'title':
            target = 'title'
        if target and tag in ('br', 'p', 'li', 'div'):
            self.parts[target].append('\n')
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.stack.append((tag, target, skip))

    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, value):
        if self.stack and self.stack[-1][1] and not self.stack[-1][2]:
            self.parts[self.stack[-1][1]].append(value)

    def value(self, name):
        lines = [re.sub(r'\s+', ' ', x).strip() for x in ''.join(self.parts[name]).splitlines()]
        return '\n'.join(x for x in lines if x)


def parse_detail(markup: str) -> dict:
    parser = DetailParser()
    parser.feed(markup)
    return {key: parser.value(key) for key in parser.parts}


def _date(value):
    if isinstance(value, (int, float)) and value > 1_000_000_000:
        try:
            return datetime.fromtimestamp(value / 1000 if value > 100_000_000_000 else value, timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError):
            pass
    return ''


def normalize(row: dict, detail: dict | None = None) -> Job:
    if not isinstance(row, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{6,100}', str(row.get('jobId', ''))):
        raise ValueError('Invalid public job ID')
    title = row.get('jobName')
    if not isinstance(title, str) or not title.strip():
        raise ValueError('Missing public job title')
    detail = detail or {}
    description = detail.get('description', '')
    substantive = isinstance(description, str) and len(description.strip()) >= 20 and re.sub(r'\s+', '', description) != re.sub(r'\s+', '', title)
    low, high = row.get('lowMonthPay'), row.get('highMonthPay')
    valid_salary = all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 10000 for v in (low, high)) and high >= low and high > 0
    salary = f'{low:g}K–{high:g}K/月' if valid_salary else '面议或未注明'
    location = str(detail.get('location') or '')
    if not location:
        raw = str(row.get('areaCodeName') or '')
        if raw in ('北京', '上海', '天津', '重庆'):
            location = raw
    evidence = {'audience': '来源主要面向高校和毕业生；具体招聘对象、毕业届次及是否接受社招仍需核对。',
                'list_region': str(row.get('areaCodeName') or ''),
                'education': str(row.get('degreeName') or ''), 'salary': salary,
                'upstream_source': str(row.get('sourcesNameCh') or '国家大学生就业服务平台'),
                'source_updated_at': _date(row.get('updateDate'))}
    if detail and not substantive:
        evidence['detail_quality'] = '已读取原始详情页，但正文仅有标题或信息过少，未标记为完整 JD。'
    job = Job(id='ncss-' + row['jobId'], title=title[:200],
              company=str(detail.get('company') or row.get('recName') or '未注明')[:200],
              city=location[:150], salary_text=salary,
              salary_min=int(low * 1000) if valid_salary else None,
              salary_max=int(high * 1000) if valid_salary else None,
              salary_basis='advertised' if valid_salary else 'unknown', salary_period='month',
              salary_source='ncss_public_monthly_range', description=description[:60000] if substantive else '',
              education=str(row.get('degreeName') or '')[:40],
              source='ncss', source_url=BASE + '/student/jobs/' + row['jobId'] + '/detail.html',
              completeness='full_jd' if substantive else 'list_only', snippet_only=False,
              evidence=evidence, posted_at=_date(row.get('publishDate')),
              collected_at=datetime.now(timezone.utc).isoformat())
    return infer_terms(job)


def collect(profile, query, emit, cancelled):
    from .providers import SourceUnavailable, public_contacts
    queries = query.get('queries', [query])
    jobs = {}
    warnings = ['国家大学生就业服务平台主要面向高校和毕业生；请核对招聘对象及岗位有效性。']
    diagnostics = {'source': 'ncss', 'queries_completed': 0, 'list_records': 0, 'full_jd': 0}
    deadline = time.monotonic() + 60
    status = 'empty'
    try:
        with httpx.Client(timeout=httpx.Timeout(12, connect=8), trust_env=False, follow_redirects=True, verify=tls_context(),
                          headers={'User-Agent': 'FanwanRadarLocal/0.2 (read-only)', 'Referer': BASE + '/student/jobs/index.html'}) as client:
            for q_index, q in enumerate(queries[:6]):
                if cancelled():
                    raise SourceUnavailable('本轮检索已取消', 'cancelled', 'cancel')
                if time.monotonic() >= deadline:
                    raise SourceUnavailable('达到本轮时间预算，已保留取得的真实记录', 'capture_timeout', 'budget')
                params = {'areaCode': region_code(str(q.get('city') or '')), 'jobName': str(q.get('keyword') or ''),
                          'offset': '1', 'limit': str(min(max(int(q.get('max_list', 10)), 1), 10))}
                emit({'stage': 'list', 'progress': q_index / max(len(queries), 1) * 100})
                response = client.get(LIST_URL, params=params)
                if response.status_code in (403, 429):
                    raise SourceUnavailable('公开来源提示访问受限，已停止；未尝试绕过', 'restricted', 'list')
                response.raise_for_status()
                if len(response.content) > 2 * 1024 * 1024:
                    raise ValueError('Response too large')
                data = response.json()
                rows = data.get('data', {}).get('list') if isinstance(data, dict) and data.get('flag') is True else None
                if not isinstance(rows, list):
                    raise SourceUnavailable('公开来源响应结构变化或本轮查询被拒绝', 'schema_error', 'list')
                candidates = []
                for row in rows:
                    job = normalize(row)
                    jobs[job.id] = job
                    candidates.append((row, job))
                    diagnostics['list_records'] += 1
                    emit({'stage': 'list', 'jobs': [job.model_dump()]})
                diagnostics['queries_completed'] += 1
                for row, job in candidates[:min(int(q.get('max_details', 2)), 3)]:
                    if cancelled():
                        raise SourceUnavailable('本轮检索已取消', 'cancelled', 'cancel')
                    if time.monotonic() >= deadline:
                        raise SourceUnavailable('达到本轮时间预算，已保留取得的真实记录', 'capture_timeout', 'budget')
                    emit({'stage': 'detail'})
                    response = client.get(job.source_url)
                    if response.status_code in (403, 429):
                        raise SourceUnavailable('详情访问受限，已停止；已保留真实列表', 'restricted', 'detail')
                    response.raise_for_status()
                    if len(response.content) > 2 * 1024 * 1024:
                        raise ValueError('Detail too large')
                    detail = parse_detail(response.text)
                    if re.search(r'安全验证|Security Verification|CAPTCHA', detail['title'], re.I):
                        raise SourceUnavailable('详情出现安全验证，已停止；未绕过验证', 'restricted', 'detail')
                    updated = normalize(row, detail)
                    if updated.completeness == 'full_jd':
                        updated.contacts = public_contacts(updated.description)
                        diagnostics['full_jd'] += 1
                    jobs[job.id] = updated
                    emit({'stage': 'detail', 'jobs': [updated.model_dump()]})
                if q_index + 1 < len(queries[:6]):
                    # A small, cancellable pause between user-targeted queries.
                    for _ in range(5):
                        if cancelled():
                            break
                        time.sleep(.1)
        status = 'partial' if jobs and diagnostics['full_jd'] < len(jobs) else 'success' if jobs else 'empty'
        if status == 'partial':
            warnings.append('部分来源详情正文缺失或尚未补齐，已保留为真实列表信息，未补造工作条件。')
    except SourceUnavailable as exc:
        diagnostics.update(failure_status=exc.status, stage=exc.stage)
        warnings.append(str(exc))
        status = 'partial' if jobs and exc.status != 'cancelled' else exc.status
    except (httpx.HTTPError, ValueError, TypeError, KeyError, OSError):
        diagnostics.update(failure_status='schema_error', stage='public_request')
        warnings.append('公开来源请求或结构校验失败，已保留已收到的真实数据；没有切换模拟结果。')
        status = 'partial' if jobs else 'schema_error'
    emit({'stage': 'finished', 'progress': 100, 'status': status})
    return {'jobs': list(jobs.values()), 'status': status, 'warnings': warnings, 'diagnostics': diagnostics}
