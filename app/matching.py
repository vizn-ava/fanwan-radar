from __future__ import annotations

import re
from .models import Job, Profile

SKILLS = ['Python', 'JavaScript', 'TypeScript', 'Java', 'SQL', 'Excel', 'Word', 'PPT',
          'PS', '剪辑', '客服', '运营', '销售', '行政', '财务', '会计', '人事',
          '招聘', '直播', '电商', '文案', '英语', '沟通', '数据分析', 'React',
          'Vue', 'FastAPI', 'AI', 'Agent', '项目管理', '仓储', '物流', '设计']
EDUCATION = {'小学': 0, '初中': 1, '中专': 2, '高中': 2, '职高': 2,
             '大专': 3, '专科': 3, '本科': 4, '硕士': 5, '博士': 6}
_NUMBER = r'\d+(?:\.\d+)?'
_AMOUNT = re.compile(rf'({_NUMBER})\s*([kK千万]?)\s*(?:[-~～—至到]\s*({_NUMBER})\s*([kK千万]?))?\s*(元)?')
_DIGITS = '零一二三四五六七八九'
_CN = r'[\d零一二两三四五六七八九十]+'


def detect_skills(text: str) -> list[str]:
    result = []
    for skill in SKILLS:
        pattern = re.escape(skill)
        if skill.isascii():
            pattern = r'(?<![A-Za-z])' + pattern + r'(?![A-Za-z])'
        if re.search(pattern, text, re.I):
            result.append(skill)
    return result


def _number(value: str) -> int:
    if value.isdigit():
        return int(value)
    value = value.replace('两', '二')
    if '十' in value:
        a, b = value.split('十', 1)
        return (_DIGITS.index(a) if a else 1) * 10 + (_DIGITS.index(b) if b else 0)
    return _DIGITS.index(value)


def _salary(text: str):
    """Return native-period amounts, never annualized or multiplied by N salaries."""
    text = text.replace(',', '').replace('，', '')
    for m in _AMOUNT.finditer(text):
        before, after = text[max(0, m.start() - 12):m.start()], text[m.end():m.end() + 12]
        periods = []
        for period, prefix, suffix in [
            ('year', r'年薪\s*[:：]?\s*$', r'^\s*(?:[/／每]年|每年|年薪)'),
            ('day', r'(?:日薪|每天|每日)\s*[:：]?\s*$', r'^\s*(?:[/／每](?:天|日)|每天|每日)'),
            ('hour', r'(?:时薪|每小时)\s*[:：]?\s*$', r'^\s*(?:[/／每](?:小时|时|h\b)|每小时)'),
            ('month', r'(?:月薪|底薪|月工资)\s*[:：]?\s*$', r'^\s*(?:[/／每]月|每月)')]:
            if re.search(prefix, before, re.I) or re.search(suffix, after, re.I):
                periods.append(period)
        if len(set(periods)) > 1:
            return None
        period = periods[0] if periods else 'month'
        # Bare numeric ranges can be dates/hours; only accept salary-sized amounts.
        unit1, unit2 = m[2] or m[4] or '', m[4] or m[2] or ''
        mul = lambda u: 1000 if u.lower() == 'k' or u == '千' else 10000 if u == '万' else 1
        lo = int(float(m[1]) * mul(unit1))
        hi = int(float(m[3] or m[1]) * mul(unit2 or unit1))
        if not (unit1 or m[5] or periods) and lo < 1000:
            continue
        if hi < lo:
            return None
        return lo, hi, period, m[0].strip()
    return None


def salary_range(text: str) -> tuple[int | None, int | None]:
    """Legacy interface: monthly range only; other periods are deliberately unknown."""
    value = _salary(text)
    return value[:2] if value and value[2] == 'month' else (None, None)


def _base_salary(text: str):
    m = re.search(r'(?:无责(?:任)?底薪|固定底薪|底薪|固定月薪)\s*[:：为]?\s*(\d[^，。；\n]*)', text)
    return (_salary(m[0]), m[0]) if m else (None, '')


def _claim(text: str, positive: str, negative: str):
    """Remove negative spans before looking for positives contained inside them."""
    negatives = list(re.finditer(negative, text, re.I))
    remaining = re.sub(negative, '', text, flags=re.I)
    positives = list(re.finditer(positive, remaining, re.I))
    quotes = [m[0] for m in negatives + positives]
    conflict = bool(negatives and positives)
    return (None if conflict else False if negatives else True if positives else None,
            '；'.join(dict.fromkeys(quotes)), conflict)


def infer_terms(job: Job) -> Job:
    """Fill missing fields from explicit statements, retaining actual evidence quotes."""
    j = job.model_copy(deep=True)
    # Titles are also source claims. Keep title/body contradictions unknown
    # instead of silently discarding a promised benefit or rest arrangement.
    text = '\n'.join(value for value in (j.title, j.description) if value)

    def put(field, value, quote=''):
        current = getattr(j, field, None)
        if current is None or current in ('', 'unknown') or current == []:
            if value is not None:
                setattr(j, field, value)
                if quote:
                    j.evidence.setdefault(field, quote)

    def claim(field, positive, negative):
        value, quote, conflict = _claim(text, positive, negative)
        if quote:
            j.evidence.setdefault(field, quote)
        current = getattr(j, field, None)
        if conflict or value is not None and current is not None and current != value:
            j.evidence['conflict_' + field] = quote
        else:
            put(field, value, quote)

    if not j.skills:
        j.skills = detect_skills(text + ' ' + j.title)
    base, base_quote = _base_salary(text)
    parsed = base or _salary(j.salary_text)
    if j.salary_min is None and parsed:
        j.salary_min = parsed[0]
        put('salary_max', parsed[1])
        put('salary_period', parsed[2])
        put('salary_basis', 'base' if base else 'advertised')
        put('salary_source', base_quote if base else j.salary_text)
        j.evidence.setdefault('salary', base_quote if base else j.salary_text)
    elif j.salary_period == 'unknown':
        headline = _salary(j.salary_text)
        if headline:
            put('salary_period', headline[2], j.salary_text)

    deny = r'(?:不|未|没有|无|非)(?:予以|予)?(?:缴纳|缴存|提供|购买|享受|保证|承诺|包含|缴|交|含)?\s*'
    claim('social_insurance', r'[五六七]险(?:一金)?|(?:缴纳|购买|提供|交)社保',
          deny + r'(?:[五六七]险(?:一金)?|社保)|(?:社保|[五六七]险(?:一金)?)\s*(?:需自理|自费|不提供|不缴纳)')
    claim('housing_fund', r'[五六七]险一金|(?:缴纳|缴存|提供|交|有|含)公积金',
          deny + r'(?:公积金|[五六七]险一金)|(?:公积金|[五六七]险一金)\s*(?:需自理|自费|不提供|不缴纳)')
    claim('insurance_during_probation',
          r'试用期(?:内|间)?(?:也|即|正常)?(?:缴纳|缴存|购买|交)?(?:社保|五险(?:一金)?)',
          r'试用期(?:内|间)?(?:不|未)(?:缴纳|缴存|购买|交)(?:社保|五险(?:一金)?)|转正后(?:才)?(?:缴纳|交)(?:社保|五险(?:一金)?)')
    m = re.search(r'公积金(?:缴存|缴纳)?基数\s*[:：为按]?\s*(\d{3,7})', text)
    if m:
        put('fund_base', int(m[1]), m[0])

    schedules = []
    for pattern, kind in [
        (r'周末双休|周六[、和及与]?周日(?:固定)?休息', 'weekend_double'),
        (r'每周(?:休息?|固定休)(?:两|二|2)天|(?<!周末)双休', 'weekly_double'),
        (r'单休|每周(?:休息?|固定休)(?:一|1)天', 'weekly_single'),
        (r'大小周|单双休|单双周轮休', 'alternating'),
        (r'轮休|排休', 'rotation')]:
        for match in re.finditer(pattern, text):
            prefix = text[max(0, match.start() - 5):match.start()]
            if re.search(r'(?:非|不|无|没有|不是|单)\s*$', prefix):
                continue
            if kind == 'weekly_single' and match.start() and text[match.start()-1] == '双':
                continue
            schedules.append((kind, match[0]))
    cycles = list(re.finditer(rf'(?:上|做|工作)\s*({_CN})\s*(?:天)?\s*(?:休|休息)\s*({_CN})\s*(?:天)?', text))
    if cycles:
        values = {(_number(m[1]), _number(m[2])) for m in cycles}
        if len(values) == 1:
            work, rest = next(iter(values))
            if 1 <= work <= 366 and 0 <= rest <= 366:
                put('cycle_work_days', work, cycles[0][0])
                put('cycle_rest_days', rest, cycles[0][0])
                schedules.append(('custom_cycle', cycles[0][0]))
        else:
            j.evidence['conflict_schedule_kind'] = '；'.join(m[0] for m in cycles)
    kinds = {kind for kind, _ in schedules}
    if 'weekend_double' in kinds:
        kinds.discard('weekly_double')
    if 'alternating' in kinds:
        kinds.discard('rotation')
    if 'custom_cycle' in kinds:
        kinds.discard('rotation')
    quote = '；'.join(dict.fromkeys(q for _, q in schedules))
    if len(kinds) > 1:
        j.evidence['conflict_schedule_kind'] = quote
    elif kinds:
        kind = next(iter(kinds))
        if j.schedule_kind not in ('unknown', kind):
            j.evidence['conflict_schedule_kind'] = quote
        put('schedule_kind', kind, quote)
        if kind in ('weekend_double', 'weekly_double', 'weekly_single'):
            put('weekly_rest_days', 1 if kind == 'weekly_single' else 2, quote)
            put('double_rest', kind != 'weekly_single', quote)
        if kind == 'weekend_double':
            put('fixed_rest_weekdays', [6, 7], quote)
    weekdays = re.search(r'每(?:周|星期)(?:固定)?(?:周|星期)?([一二三四五六日天])(?:[、和与及](?:周|星期)?([一二三四五六日天]))?(?:固定)?休息', text)
    if weekdays:
        days = ['一二三四五六日'.index(d.replace('天', '日')) + 1 for d in weekdays.groups() if d]
        put('fixed_rest_weekdays', days, weekdays[0])
        put('weekly_rest_days', len(days), weekdays[0])
        put('schedule_kind', 'weekend_double' if set(days) == {6, 7} else 'weekly_double' if len(days) == 2 else 'weekly_single', weekdays[0])
    hours = list(re.finditer(rf'(?:每天|每日|日工作|工作时长(?:为)?|工作)?\s*({_CN}(?:\.\d+)?)\s*(?:小时|h)\s*(?:工作制|制|/天)?', text, re.I))
    values = set()
    for m in hours:
        # Ignore weekly hours, overtime duration and breaks rather than inventing a daily total.
        if re.search(r'每周|加班|休息|午休', text[max(0, m.start()-4):m.start()] + m[0][:4]):
            continue
        v = float(m[1]) if re.fullmatch(_NUMBER, m[1]) else _number(m[1])
        if 0 < v <= 24:
            values.add(v)
    if len(values) == 1:
        if j.work_hours_daily is not None and j.work_hours_daily not in values:
            j.evidence['conflict_work_hours_daily'] = '；'.join(m[0] for m in hours)
        put('work_hours_daily', next(iter(values)), '；'.join(m[0] for m in hours))
    elif len(values) > 1:
        j.evidence['conflict_work_hours_daily'] = '；'.join(m[0] for m in hours)
    for field, pos, neg in [
        ('night_shift', r'夜班|晚班|通宵', r'(?:无|没有|不安排|无需|不用|不需要|不接受)\s*(?:夜班|晚班|通宵)|仅白班|长白班'),
        ('rotating_shift', r'倒班|轮班|轮岗班|两班倒|三班倒|两班制|三班制', r'(?:无|没有|无需|不用|不需要|不)\s*(?:倒班|轮班)|固定白班|长白班'),
        ('overtime', r'加班', r'(?:无|没有|无需|不用|不需要|不安排|不)\s*加班'),
        ('outsourced', r'外包', r'(?:非|无|不是|不属于|不涉及)\s*外包'),
        ('dispatched', r'(?:劳务)?派遣', r'(?:非|无|不是|不属于|不涉及)\s*(?:劳务)?派遣')]:
        claim(field, pos, neg)
    m = re.search(r'(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)\s*[-~～至到]\s*([01]?\d|2[0-3])[:：]([0-5]\d)', text)
    if m:
        put('work_start', f'{int(m[1]):02d}:{m[2]}', m[0])
        put('work_end', f'{int(m[3]):02d}:{m[4]}', m[0])
    types = [m for m in re.finditer(r'全职|兼职|实习|临时工|劳务派遣', text)
             if not re.search(r'(?:非|不|无|不是)\s*$', text[max(0, m.start()-4):m.start()])]
    if len({m[0] for m in types}) == 1:
        put('employment_type', types[0][0], types[0][0])
    m = re.search(r'(?:所属)?行业\s*[:：]\s*([^，。；\n]+)', text)
    if m:
        put('industry', m[1].strip(), m[0])
    if not j.education:
        if re.search(r'学历不限|不限学历|无学历要求', text):
            put('education', '不限', re.search(r'学历不限|不限学历|无学历要求', text)[0])
        else:
            m = re.search(r'(博士|硕士|本科|大专|专科|高中|中专|初中)(?:及以上|以上|学历|毕业)', text)
            if m:
                put('education', m[1], m[0])
    if j.experience_min is None:
        m = re.search(rf'({_CN}(?:\.\d+)?)\s*(?:[-~至到]\s*{_CN})?\s*年(?:及以上|以上)?(?:相关)?(?:工作)?经验', text)
        if m:
            put('experience_min', float(m[1]) if re.fullmatch(_NUMBER, m[1]) else _number(m[1]), m[0])
        elif re.search(r'经验不限|不限经验|无需经验|无经验要求|接受应届生', text):
            put('experience_min', 0, re.search(r'经验不限|不限经验|无需经验|无经验要求|接受应届生', text)[0])
    # Legacy snippet_only also marks native list rows. Preserve their explicit
    # provenance instead of relabelling platform API lists as search-engine text.
    if j.snippet_only and j.completeness != 'list_only':
        j.completeness = 'search_snippet'
    return j


def _schedule_status(job, accepted):
    kind = job.schedule_kind
    if 'conflict_schedule_kind' in job.evidence:
        return 'unknown'
    outcomes = []
    for schedule in accepted:
        s = schedule.model_dump() if hasattr(schedule, 'model_dump') else schedule
        wanted = s['kind']
        if kind == 'unknown':
            result = None
        elif wanted == 'weekly_double':
            result = kind in ('weekend_double', 'weekly_double')
        elif wanted == 'weekend_double' and kind == 'weekly_double':
            result = set(job.fixed_rest_weekdays) == {6, 7} if job.fixed_rest_weekdays else None
        else:
            result = kind == wanted
        if result and wanted == 'custom_cycle':
            result = None if any(v is None for v in [job.cycle_work_days, job.cycle_rest_days,
                                                      s.get('work_days'), s.get('rest_days')]) else (
                job.cycle_work_days == s.get('work_days') and job.cycle_rest_days == s.get('rest_days'))
        if result and s.get('fixed_weekdays'):
            result = set(job.fixed_rest_weekdays) == set(s['fixed_weekdays']) if job.fixed_rest_weekdays else None
        outcomes.append(result)
    return 'pass' if True in outcomes else 'unknown' if None in outcomes else 'fail'


def analyze(job: Job, p: Profile) -> dict:
    job = infer_terms(job)
    checks = []
    priorities = getattr(p, 'priorities', {})
    pending = set(getattr(p, 'confirmation_required', []))
    aliases = {
        'location': ['city', 'cities', 'districts', 'allow_relocation'],
        'salary': ['salary_min', 'salary_max', 'salary_period', 'salary_basis'],
        'insurance': ['insurance_requirement', 'insurance_during_probation', 'fund_base_min', 'require_housing_fund'],
        'schedule': ['accepted_schedules', 'require_double_rest'], 'hours': ['max_daily_hours'],
        'night_shift': ['accept_night_shift'], 'rotating_shift': ['accept_rotating_shift'],
        'overtime': ['accept_overtime'], 'work_time': ['work_start', 'work_end'],
        'employment': ['employment_types'], 'industry': ['industries'],
        'outsourcing': ['accept_outsourcing'], 'dispatch': ['accept_dispatch'],
        'remote': ['allow_remote'], 'commute': ['max_commute_minutes'],
        'role': ['target_roles'], 'skills': ['skills'], 'education': ['education'], 'experience': ['experience_years']}

    def add(key, label, status, evidence, fields=(), group=None, force=False, default_priority='must'):
        group = group or key
        priority = 'must' if force else priorities.get(group, default_priority)
        if priority == 'ignore':
            return
        if pending.intersection([group, key] + aliases.get(group, [])):
            status, evidence = 'unknown', '个人条件待确认，不能用于确认匹配；' + evidence
        for field in fields:
            if 'conflict_' + field in job.evidence:
                status = 'unknown'
                evidence += '；原文存在矛盾：' + job.evidence['conflict_' + field]
        quotes = list(dict.fromkeys(job.evidence[f] for f in fields if job.evidence.get(f)))
        if quotes:
            evidence += '；原文：' + '；'.join(quotes)
        checks.append(dict(key=key, label=label, status=status, evidence=evidence, priority=priority))

    def compare(value, predicate):
        return 'unknown' if value is None else 'pass' if predicate(value) else 'fail'

    remote_value, remote_quote, remote_conflict = _claim(
        job.city + ' ' + job.title + ' ' + job.description, r'远程|remote',
        r'不支持远程|非远程|不可远程|不接受远程|无远程|现场办公|到岗办公|线下办公|坐班',)
    cities = getattr(p, 'cities', []) or [c for c in re.split(r'[,，、/或\s]+', p.city) if c]
    districts = getattr(p, 'districts', [])
    if cities or districts:
        status = 'unknown'
        if remote_value is not True and not remote_conflict and job.city:
            city_ok = not cities or any(c.removesuffix('市') in job.city for c in cities)
            district_ok = not districts or any(d in job.city for d in districts)
            # A city-only location cannot establish an absent district.
            status = 'pass' if city_ok and district_ok else 'unknown' if city_ok and districts else 'fail'
            if city_ok and districts and not district_ok and re.search(r'(?:区|县|镇)(?:\s|$|[·,，/])', job.city):
                status = 'fail'
            if not city_ok and getattr(p, 'allow_relocation', None) is True:
                status = 'unknown'
        add('location', '工作地点', status,
            f'实际地点：{job.city or "未提供"}；期望城市：{cities}；区域：{districts}' +
            ('；远程雇佣地区、资格及时区限制待确认' if remote_value or remote_conflict else ''))
    if getattr(p, 'allow_remote', None) is not None:
        status = 'unknown' if remote_conflict or remote_value is None else (
            'unknown' if remote_value and p.allow_remote else 'fail' if remote_value else 'pass')
        add('remote', '远程工作', status, (remote_quote or '未明确远程或现场工作方式') + '；远程岗位雇佣资格和地区限制需核实')
    if p.salary_min or getattr(p, 'salary_max', None) is not None:
        lo, hi, period, basis = job.salary_min, job.salary_max, job.salary_period, job.salary_basis
        quote = job.salary_source or job.salary_text
        if p.salary_basis == 'base' and basis != 'base':
            base, base_quote = _base_salary(job.description)
            if base:
                lo, hi, period, basis, quote = *base[:3], 'base', base_quote
        elif p.salary_basis == 'advertised' and basis == 'base':
            advertised = _salary(job.salary_text)
            if advertised:
                lo, hi, period, basis, quote = *advertised[:3], 'advertised', job.salary_text
        status = 'unknown'
        if lo is not None and period == getattr(p, 'salary_period', 'month') and (p.salary_basis != 'base' or basis == 'base'):
            if hi is not None and hi < p.salary_min or getattr(p, 'salary_max', None) is not None and lo > p.salary_max:
                status = 'fail'
            elif lo >= p.salary_min and (getattr(p, 'salary_max', None) is None or hi is not None and hi <= p.salary_max):
                status = 'pass'
        add('salary', '薪资条件', status,
            f'来源：{quote}；金额 {lo}–{hi}，周期 {period}，口径 {basis}；期望周期 {p.salary_period}、口径 {p.salary_basis}；区间上限不代表承诺收入', ['salary'])
    insurance = getattr(p, 'insurance_requirement', 'any')
    # Explicit legacy conditions remain usable; ambiguous migrated defaults are pending.
    if insurance == 'any' and getattr(p, 'require_housing_fund', None) and 'insurance_requirement' not in p.model_fields_set:
        insurance = 'five_and_fund'
    if insurance != 'any':
        values = [job.social_insurance] + ([job.housing_fund] if insurance == 'five_and_fund' else [])
        status = 'fail' if False in values else 'unknown' if None in values else 'pass'
        add('insurance', '社保及公积金', status, f'要求 {insurance}；社保 {job.social_insurance}；公积金 {job.housing_fund}', ['social_insurance', 'housing_fund'])
    if getattr(p, 'insurance_during_probation', None) is not None:
        add('insurance_probation', '试用期参保', compare(job.insurance_during_probation, lambda v: v == p.insurance_during_probation),
            f'试用期参保：{job.insurance_during_probation}；未说明时不得按正式期福利推断', ['insurance_during_probation'], 'insurance')
    if getattr(p, 'fund_base_min', None) is not None:
        add('fund_base', '公积金基数', compare(job.fund_base, lambda v: v >= p.fund_base_min),
            f'公积金基数：{job.fund_base}；最低要求：{p.fund_base_min}', ['fund_base'], 'insurance')
    schedules = getattr(p, 'accepted_schedules', [])
    if not schedules and getattr(p, 'require_double_rest', None) and 'accepted_schedules' not in p.model_fields_set:
        schedules = [{'kind': 'weekend_double'}]
    if schedules:
        add('schedule', '休息制度', _schedule_status(job, schedules),
            f'制度 {job.schedule_kind}；上 {job.cycle_work_days} 休 {job.cycle_rest_days}；固定休息日 {job.fixed_rest_weekdays}；每周两天休息不等于周末双休', ['schedule_kind', 'fixed_rest_weekdays'])
    if getattr(p, 'max_daily_hours', None) is not None:
        add('hours', '每日工时', compare(job.work_hours_daily, lambda v: v <= p.max_daily_hours),
            f'日工作时长 {job.work_hours_daily} 小时；上限 {p.max_daily_hours}；上下班跨度不能当作实际工时', ['work_hours_daily'])
    for group, profile_field, job_field, label in [
        ('night_shift', 'accept_night_shift', 'night_shift', '夜班'),
        ('rotating_shift', 'accept_rotating_shift', 'rotating_shift', '倒班'),
        ('overtime', 'accept_overtime', 'overtime', '加班'),
        ('outsourcing', 'accept_outsourcing', 'outsourced', '外包'),
        ('dispatch', 'accept_dispatch', 'dispatched', '派遣')]:
        accepted = getattr(p, profile_field, None)
        if accepted is False or pending.intersection([group, profile_field]):
            value = getattr(job, job_field)
            add(group, label, compare(value, lambda v: not v), f'岗位{label}：{value}；个人不接受', [job_field])
    if getattr(p, 'work_start', '') or getattr(p, 'work_end', ''):
        known = (not p.work_start or job.work_start) and (not p.work_end or job.work_end)
        fits = (not p.work_start or job.work_start >= p.work_start) and (not p.work_end or job.work_end <= p.work_end)
        overnight = job.work_start and job.work_end and job.work_end < job.work_start
        add('work_time', '工作时间', 'unknown' if not known or overnight else 'pass' if fits else 'fail',
            f'岗位 {job.work_start or "未知"}–{job.work_end or "未知"}；可接受 {p.work_start or "未设"}–{p.work_end or "未设"}', ['work_start', 'work_end'])
    for group, profile_field, job_field, label in [
        ('employment', 'employment_types', 'employment_type', '用工类型'),
        ('industry', 'industries', 'industry', '行业')]:
        wanted = getattr(p, profile_field, [])
        if wanted:
            value = getattr(job, job_field)
            add(group, label, 'unknown' if not value else 'pass' if value in wanted else 'fail',
                f'岗位：{value or "未明确"}；接受：{wanted}', [job_field])
    if p.target_roles:
        matched = [r for r in p.target_roles if r.casefold() in job.title.casefold()]
        add('role', '目标岗位', 'pass' if matched else 'unknown', f'岗位名称：{job.title}；直接命中：{matched or "无，职责需核实"}')
    matching_skills = [s for s in p.skills if s.casefold() in {x.casefold() for x in job.skills}]
    if p.skills:
        add('skills', '技能适配', 'pass' if matching_skills else 'unknown',
            '已提供技能交集：' + '、'.join(matching_skills) if matching_skills else '未发现显式技能交集，不等于能力不符；可进一步了解岗位实际使用的工具和技能',
            group='skills', default_priority='prefer')
    if p.education:
        status = 'pass' if job.education in ('不限', '学历不限') else (
            'unknown' if p.education not in EDUCATION or job.education not in EDUCATION else
            'pass' if EDUCATION[p.education] >= EDUCATION[job.education] else 'fail')
        add('education', '学历要求', status, f'岗位：{job.education or "未说明"}；个人：{p.education or "未填写"}', ['education'])
    if p.experience_years is not None:
        status = 'pass' if job.experience_min == 0 else (
            'unknown' if p.experience_years is None or job.experience_min is None else
            'pass' if p.experience_years >= job.experience_min else 'fail')
        add('experience', '经验要求', status, f'岗位最低年限：{job.experience_min}；个人年限：{p.experience_years}', ['experience_min'])
    for term in getattr(p, 'excluded_terms', []):
        value, quote, conflict = _claim(job.title + '；' + job.description, re.escape(term),
            r'(?:不涉及|不含|无需|没有|不做|不要求|不是|非|无)\s*' + re.escape(term))
        status = 'unknown' if conflict or value is None else 'fail' if value else 'pass'
        add('exclusion_' + term, '排除' + term, status, quote or f'未说明是否涉及{term}，不能证明不存在', group='excluded_terms')
    if getattr(p, 'max_commute_minutes', None) is not None:
        add('commute', '通勤时间', 'unknown', '未接地图路线服务，无法核实实际地址、出发地及通勤时长')

    substantive = bool(p.target_roles or p.education or p.experience_years is not None or cities or districts
                       or p.salary_min or getattr(p, 'salary_max', None) is not None or insurance != 'any' or schedules
                       or any(getattr(p, field, None) not in (None, '', []) for field in
                              ['max_daily_hours', 'work_start', 'work_end', 'employment_types', 'industries',
                               'excluded_terms', 'max_commute_minutes', 'fund_base_min', 'insurance_during_probation'])
                       or any(getattr(p, field, None) is False for field in
                              ['accept_night_shift', 'accept_rotating_shift', 'accept_overtime', 'accept_outsourcing', 'accept_dispatch'])
                       or getattr(p, 'allow_remote', None) is not None)
    if not substantive or not checks:
        add('profile', '个人条件', 'unknown', '尚无生效的个人条件，请补充求职要求后判断', force=True)
    if job.completeness in ('list_only', 'search_snippet') or not job.description.strip():
        add('completeness', '信息完整度', 'unknown',
            f'来源完整度：{job.completeness}；' + ('仅列表/摘要，需获取完整 JD' if job.completeness in ('list_only', 'search_snippet') else '未提供岗位描述'), force=True)
    failed = [c for c in checks if c['status'] == 'fail']
    unknown = [c for c in checks if c['status'] == 'unknown']
    must_failed = [c for c in failed if c['priority'] == 'must']
    must_unknown = [c for c in unknown if c['priority'] == 'must']
    verdict = 'mismatch' if must_failed else 'needs_confirmation' if must_unknown else 'match'
    weights = {'must': 2, 'prefer': 1}
    score = round(100 * sum(weights[c['priority']] * {'pass': 1, 'unknown': .35, 'fail': 0}[c['status']] for c in checks)
                  / max(1, sum(weights[c['priority']] for c in checks)))
    if must_failed:
        score = min(score, 49)
    elif must_unknown:
        score = min(score, 79)
    strengths = [c['label'] + '：' + c['evidence'] for c in checks if c['status'] == 'pass']
    summary = '存在必须条件不符' if must_failed else '必须条件或信息尚待确认' if must_unknown else '已提供信息符合生效的必须条件，偏好仅用于排序'
    return dict(score=score, verdict=verdict, checks=checks, strengths=strengths,
                gaps=[c['label'] + '：' + c['evidence'] for c in failed + unknown],
                questions=[f'请确认{c["label"]}：{c["evidence"]}' for c in unknown + failed],
                summary=summary + '。分数仅用于本次排序，不代表录用概率或岗位真实性认证。')
