from __future__ import annotations

import hashlib
import os
import re
from urllib.parse import urlsplit, urlunsplit

import httpx
from .models import Contact, Job
from .matching import infer_terms
from .query_planner import build_search_plan
from .network import tls_context


class SourceUnavailable(RuntimeError):
    def __init__(self, message, status='unavailable', stage='preflight', diagnostics=None):
        super().__init__(message)
        self.status = status
        self.stage = stage
        # Callers supply only allowlisted counters/codes, never raw exception text.
        self.diagnostics = diagnostics or {}


def cdp_preflight():
    try:
        port = int(os.getenv('JOB_CDP_PORT', '9222'))
        if not 1 <= port <= 65535:
            raise ValueError
        with httpx.Client(timeout=1.5, trust_env=False, verify=tls_context()) as client:
            response = client.get(f'http://127.0.0.1:{port}/json/version')
            response.raise_for_status()
            data = response.json()
        ws = urlsplit(data.get('webSocketDebuggerUrl', ''))
        if (not data.get('Browser') or not data.get('Protocol-Version') or ws.scheme != 'ws'
                or ws.hostname not in ('127.0.0.1', 'localhost', '::1') or ws.port != port
                or not ws.path.startswith('/devtools/browser/')):
            raise ValueError
        return data
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise SourceUnavailable('专用浏览器 CDP 协议未就绪；请运行登录脚本。', 'cdp_error', diagnostics={'protocol_valid': False}) from None


def boss_available():
    try:
        cdp_preflight()
        return True
    except SourceUnavailable:
        return False


def source_statuses():
    connected = boss_available()
    return [dict(id='boss', name='BOSS直聘', status='available' if connected else 'cdp_error',
                 description='CDP 协议已连接；登录及平台访问权限尚未验证。' if connected else '需启动专用浏览器并本人登录；端口连接不是登录验证。',
                 capabilities=['passive_list', 'bounded_full_jd', 'partial_results', 'cancellation']),
            dict(id='ncss', name='国家大学生就业服务平台', status='available',
                 description='已验证公开岗位列表和原始详情链接；主要面向高校/毕业生，部分岗位正文缺失，具体条件仍需核对。',
                 capabilities=['search', 'list', 'detail', 'partial_results', 'cancellation']),
            *[dict(id=s, name=n, status='unavailable', description=reason, capabilities=[])
              for s, n, reason in [
                  ('liepin', '猎聘', '实测公开 HTML 仅有 WebPage 结构化数据，浏览器进入安全中心验证码；已停止，未启用。'),
                  ('zhaopin', '智联招聘', '实测公开搜索返回 Security Verification；已停止，未启用。'),
                  ('public', '搜索摘要（未启用）', '搜索摘要不能代表已获取招聘平台完整 JD；未启用。')]]]


def public_contacts(text):
    contacts = [Contact(kind='email', value=e[:400]) for e in dict.fromkeys(re.findall(
        r'(?<![A-Za-z0-9_.+-])[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text))]
    for phone in dict.fromkeys(re.findall(r'(?<!\d)1[3-9]\d{9}(?!\d)', text)):
        if re.search(r'(?:联系电话|招聘电话|联系手机|联系方式|手机)\s*[:：]?\s*' + phone, text):
            contacts.append(Contact(kind='phone', value=phone))
    return contacts[:20]


def clean_job_url(url, source='boss'):
    try:
        p = urlsplit(url)
        domains = {'boss': 'zhipin.com', 'liepin': 'liepin.com', 'zhaopin': 'zhaopin.com'}
        domain = domains.get(source, '')
        host = (p.hostname or '').lower()
        if (p.scheme not in ('http', 'https') or p.username or p.password or not domain
                or not (host == domain or host.endswith('.' + domain))):
            return ''
        if source == 'boss' and not re.fullmatch(r'/job_detail/[A-Za-z0-9_~-]+\.html', p.path):
            return ''
        return urlunsplit((p.scheme, host, p.path, '', ''))
    except ValueError:
        return ''


def normalize_boss(raw, detail):
    if not isinstance(raw, dict) or not isinstance(detail, dict):
        raise SourceUnavailable('岗位结构异常', 'schema_error', 'normalize')
    encrypted = raw.get('encryptJobId') or raw.get('encrypt_job_id') or ''
    url = clean_job_url(str(raw.get('job_link') or detail.get('job_link') or ''))
    if not url and isinstance(encrypted, str) and re.fullmatch(r'[A-Za-z0-9_~-]{8,200}', encrypted):
        url = f'https://www.zhipin.com/job_detail/{encrypted}.html'
    title = raw.get('jobName') or raw.get('title') or detail.get('title')
    if not isinstance(title, str) or not title.strip() or not url:
        raise SourceUnavailable('岗位缺少真实详情链接或名称', 'schema_error', 'normalize')

    def strings(value):
        if isinstance(value, str):
            return [x.strip() for x in value.split('|') if x.strip()]
        return [x for x in value if isinstance(x, str)] if isinstance(value, list) else []

    if detail.get('jd') is not None and not isinstance(detail.get('jd'), str):
        raise SourceUnavailable('详情正文结构异常，未将对象转换成职位描述', 'schema_error', 'normalize')
    jd = detail.get('jd') or ''
    welfare = strings(raw.get('welfareList') or raw.get('welfare'))
    description = jd + ('\n列表福利标签：' + '、'.join(welfare) if welfare else '')
    tags = strings(raw.get('tags'))
    education = str(raw.get('jobDegree') or next((t for t in tags if re.search('本科|大专|硕士|博士|高中|中专|学历不限', t)), ''))
    experience = str(raw.get('jobExperience') or next((t for t in tags if '年' in t or '经验' in t or '应届' in t), ''))
    years = re.search(r'(\d+)\s*(?:[-~至]\s*\d+)?\s*年', experience)
    salary = str(raw.get('salaryDesc') or raw.get('salary') or detail.get('salary') or '未注明')
    period = 'year' if re.search('年薪|[/／]年', salary) else 'day' if re.search('日薪|[/／][天日]', salary) else 'hour' if re.search('时薪|[/／](?:小?时|h)', salary) else 'month' if re.search('[kK千万]|[/／]月', salary) else 'unknown'
    evidence = {'list_tags': ' | '.join(tags), 'experience': experience, 'education': education,
                'welfare': '、'.join(welfare), 'salary': salary}
    payload = dict(id='boss-' + hashlib.sha256(url.encode()).hexdigest()[:18], title=title[:200],
                   company=str(raw.get('brandName') or raw.get('boss_name') or detail.get('company') or '未注明')[:200],
                   city=str(raw.get('location') or '·'.join(str(raw.get(k) or '') for k in ('cityName', 'areaDistrict', 'businessDistrict'))).strip('·')[:150],
                   salary_text=salary[:150], salary_source='boss_list_api' if raw.get('salaryDesc') or raw.get('salary_source') == 'api' else 'boss_list_unverified',
                   salary_period=period, description=description[:60000],
                   skills=list(dict.fromkeys(strings(raw.get('skills')) + strings(detail.get('tags')) + strings(detail.get('tags_list')) + strings(detail.get('skills')) + strings(detail.get('skill_tags'))))[:100],
                   education=education[:40], experience_min=float(years[1]) if years else 0 if re.search('不限|应届|无经验', experience) else None,
                   source='boss', source_url=url, contacts=public_contacts(jd), snippet_only=not bool(jd),
                   completeness='full_jd' if jd else 'list_only', evidence=evidence,
                   industry=str(raw.get('brandIndustry') or raw.get('company_industry') or ''), posted_at='')
    validated = Job.model_validate(payload)
    try:
        job = infer_terms(validated)
    except (AttributeError, ValueError, TypeError):
        # Enrichment must never discard a validated original record.
        job = validated
        job.evidence['enrichment_warning'] = '原文已保留，条件提取失败，待核对'
    job.completeness = 'full_jd' if jd else 'list_only'
    # Older matching versions treat all numbers as monthly. Do not mislabel them.
    if period != 'month' and job.salary_period != period:
        job.salary_min = job.salary_max = None
    job.salary_period = period
    return job


def collect_source(source, profile, query, emit, cancelled):
    if source == 'ncss':
        from .source_ncss import collect
        return collect(profile, query, emit, cancelled)
    from .collector_worker import collect_source as worker
    return worker(source, profile, query, emit, cancelled)


def boss_search(profile, keyword):
    plan = build_search_plan(profile, keyword, ['boss'])
    result = collect_source('boss', profile, plan['sources'][0], lambda event: None, lambda: False)
    if not result['jobs'] and result['status'] not in ('empty', 'success'):
        raise SourceUnavailable('BOSS 本次采集未完成，请查看专用浏览器。', result['status'], diagnostics=result['diagnostics'])
    return result['jobs'], result['warnings']


async def public_search(profile, keyword):
    raise SourceUnavailable('搜索摘要不是已支持的招聘平台，请选择真实来源或导入原文。')
