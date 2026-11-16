"""Bounded recall-oriented platform searches; exact conditions stay in matching."""
from __future__ import annotations

import re

# BOSS advertised monthly bins. Include every overlapping bin, not an exact wage.
SALARY_BINS = [(0, 3000, '402'), (3000, 5000, '403'), (5000, 10000, '404'),
               (10000, 20000, '405'), (20000, 50000, '406'), (50000, None, '407')]
CONDITIONS = {
    'location': ['cities', 'city', 'districts', 'allow_relocation'],
    'salary': ['salary_min', 'salary_max', 'salary_period', 'salary_basis'],
    'insurance': ['insurance_requirement', 'insurance_during_probation', 'fund_base_min'],
    'schedule': ['accepted_schedules'], 'hours': ['max_daily_hours'],
    'night_shift': ['accept_night_shift'], 'rotating_shift': ['accept_rotating_shift'],
    'overtime': ['accept_overtime'], 'work_time': ['work_start', 'work_end'],
    'employment': ['employment_types'], 'industry': ['industries'],
    'excluded_terms': ['excluded_terms'], 'outsourcing': ['accept_outsourcing'],
    'dispatch': ['accept_dispatch'], 'remote': ['allow_remote'],
    'commute': ['max_commute_minutes'], 'role': ['target_roles'],
    'education': ['education'], 'experience': ['experience_years'],
}


def build_search_plan(profile, keyword='', sources=None):
    p = profile.model_dump() if hasattr(profile, 'model_dump') else dict(profile)
    roles = [keyword.strip()] if keyword.strip() else p.get('target_roles', [])[:3]
    if not roles:
        roles = p.get('skills', [])[:2]
    cities = p.get('cities') or [s for s in re.split(r'[,，、/\s]+', p.get('city', '')) if s]
    cities = cities[:3] or ['']
    terms = list(dict.fromkeys(str(r).strip()[:100] for r in roles if str(r).strip()))
    post = {}
    pending = set(p.get('confirmation_required', []))
    for key, fields in CONDITIONS.items():
        if key == 'salary' and not p.get('salary_min') and p.get('salary_max') is None:
            continue
        values = {f: p[f] for f in fields if f in p and p[f] is not None
                  and p[f] != '' and p[f] != [] and p[f] != 'any'
                  and not (f == 'salary_min' and p[f] == 0)}
        if values:
            post[key] = {'values': values, 'priority': p.get('priorities', {}).get(key, 'must'),
                         'pending_confirmation': key in pending or any(f in pending for f in fields)}
    lo, hi = p.get('salary_min', 0), p.get('salary_max')
    # Base salary, other periods and pending conditions cannot safely map to headline bins.
    bins = []
    if (lo or hi) and p.get('salary_period', 'month') == 'month' and p.get('salary_basis') == 'advertised' and not post.get('salary', {}).get('pending_confirmation') and post.get('salary', {}).get('priority') == 'must':
        bins = [code for a, b, code in SALARY_BINS if (b is None or b >= lo) and (hi is None or a <= hi)]
    plan_warnings = []
    if len(bins) > 1:
        bins = []
        plan_warnings.append('工资要求跨越平台多个薪资档位，本轮不使用未经验证的多档参数；先按城市和岗位检索，再按原文核对薪资，避免漏掉候选。')
    per_source = []
    for source in dict.fromkeys(sources if sources is not None else ['boss']):
        queries = []
        if source in ('boss', 'liepin', 'zhaopin', 'ncss'):
            for city in cities:
                for term in terms:
                    platform_filters = {'salary': bins[0]} if source == 'boss' and bins else {}
                    if source == 'ncss':
                        from .source_ncss import region_code
                        try:
                            platform_filters = {'areaCode': region_code(city), 'jobName': term}
                        except ValueError:
                            platform_filters = {'unsupported_city': city}
                    queries.append({'keyword': term, 'city': city, 'platform_filters': platform_filters,
                                    'max_details': 2, 'max_list': 10 if source == 'ncss' else 20})
        per_source.append({'source': source, 'queries': queries[:6], 'post_filters': post,
                           'enabled': source in ('boss', 'ncss'),
                           'reason': '主要面向高校和毕业生；具体招聘对象及详情完整度需核对' if source == 'ncss' else '' if source == 'boss' else '尚未通过真实列表及完整 JD 验证，未启用'})
    return {'sources': per_source, 'post_filters': post, 'query_count': sum(len(s['queries']) for s in per_source),
            'warnings': plan_warnings + ([] if terms else ['请填写目标岗位、技能或搜索词']),
            'notes': ['平台学历和经验均不限；按个人可胜任的最低门槛后置判断。',
                      '薪资采用宽区间召回，底薪及福利、作息、排除项按完整原文后置核对。']}
