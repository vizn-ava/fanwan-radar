"""Readable model context; preserve original recruitment text verbatim."""
from copy import deepcopy
import re

FIELD_LABELS = {
    'city': '主要意向城市', 'cities': '意向城市', 'districts': '意向区县',
    'target_roles': '意向岗位', 'skills': '本人已有技能', 'education': '本人学历',
    'experience_years': '本人工作年限', 'salary_min': '期望薪资下限', 'salary_max': '期望薪资上限',
    'salary_period': '计薪周期', 'salary_basis': '工资口径', 'insurance_requirement': '保险要求',
    'insurance_during_probation': '试用期缴纳要求', 'fund_base_min': '公积金基数下限',
    'accepted_schedules': '可接受休息安排', 'max_daily_hours': '每日工时上限',
    'accept_night_shift': '是否接受夜班', 'accept_rotating_shift': '是否接受倒班',
    'accept_overtime': '是否接受加班', 'work_start': '可接受上班时间', 'work_end': '可接受下班时间',
    'employment_types': '用工类型', 'industries': '意向行业', 'excluded_terms': '排除关键词',
    'accept_outsourcing': '是否接受外包', 'accept_dispatch': '是否接受派遣',
    'allow_remote': '是否接受远程', 'allow_relocation': '是否接受异地搬迁',
    'max_commute_minutes': '通勤上限（分钟）', 'confirmation_required': '尚待本人确认的历史字段',
    'priorities': '条件重要性',
}
GROUP_LABELS = dict(location='地点', salary='工资', insurance='社保公积金', schedule='休息安排',
                    hours='工时', night_shift='夜班', rotating_shift='倒班', overtime='加班',
                    work_time='上下班时间', employment='用工形式', industry='行业',
                    excluded_terms='排除词', outsourcing='外包', dispatch='派遣', remote='远程',
                    commute='通勤', role='岗位方向', education='学历', experience='经验', skills='技能参考')
VALUE_LABELS = {
    'must': '硬性条件', 'prefer': '偏好参考（不作为否决）', 'ignore': '不参与筛选',
    'pass': '已有依据支持', 'fail': '明确不符', 'unknown': '待确认',
    'match': '较匹配', 'needs_confirmation': '待确认', 'mismatch': '存在硬性不符',
    'weekly_double': '每周休两天（不限定周末或连续休）', 'weekend_double': '固定周六周日双休',
    'weekly_single': '每周休一天', 'alternating': '大小周或单双休', 'rotation': '轮休',
    'custom_cycle': '自定义上几休几', 'five_and_fund': '五险一金', 'five_insurances': '五险', 'any': '不限',
    'unspecified': '工资口径未指定（不代表要求固定底薪）', 'base': '固定底薪', 'advertised': '招聘标价',
    'month': '月', 'year': '年', 'day': '天', 'hour': '小时',
    'full_time': '全职', 'part_time': '兼职', 'project': '项目制',
    'full_jd': '已有完整职位原文', 'list_only': '只有平台列表信息',
    'search_snippet': '只有搜索摘要', 'user_provided': '用户提供，未独立核验',
    'True': '是', 'False': '否', 'None': '未提供',
}
_TOKENS = re.compile(r'\b(' + '|'.join(re.escape(k) for k in sorted(VALUE_LABELS, key=len, reverse=True)) + r')\b')


def _reason(value):
    if not isinstance(value, str):
        return value
    # Translate only generated rule prose, never the appended original quote.
    parts = re.split(r'(；原文(?:存在矛盾)?：)', value, maxsplit=1)
    generated = parts[0]
    separator, original = (parts[1], parts[2]) if len(parts) == 3 else ('', '')
    return _TOKENS.sub(lambda match: VALUE_LABELS[match[0]], generated) + separator + original


def readable_context(context: dict) -> dict:
    result = deepcopy(context)
    result['休息概念校核'] = {
        '每周休两天': '每个自然周都休两天，不限定周末，也不保证连续休。',
        '大小周或单双休': '交替出现单休周和双休周，含有只休一天的周，因此不满足每周都休两天。',
        '口径冲突': '标题说双休、正文说单双休时，原文存在冲突，应待确认，不能据此判断符合。',
    }
    profile = result['saved_profile']
    for field in ('salary_period', 'salary_basis', 'insurance_requirement'):
        profile[field] = VALUE_LABELS.get(profile.get(field), profile.get(field))
    profile['employment_types'] = [VALUE_LABELS.get(v, v) for v in profile.get('employment_types', [])]
    profile['accepted_schedules'] = [
        {'安排': VALUE_LABELS.get(s.get('kind'), s.get('kind')), '每周期工作天数': s.get('work_days'),
         '每周期休息天数': s.get('rest_days'), '固定休息星期': s.get('fixed_weekdays', [])}
        for s in profile.get('accepted_schedules', [])]
    profile['priorities'] = {GROUP_LABELS.get(k, k): VALUE_LABELS.get(v, v) for k, v in profile.get('priorities', {}).items()}
    profile['confirmation_required'] = [FIELD_LABELS.get(k, k) for k in profile.get('confirmation_required', [])]
    result['saved_profile'] = {FIELD_LABELS.get(k, k): v for k, v in profile.items()
                               if k not in {'field_sources', 'require_double_rest', 'require_housing_fund'}}
    for job in result.get('jobs', []):
        for field in ('salary_period', 'salary_basis', 'completeness'):
            job[field] = VALUE_LABELS.get(job.get(field), job.get(field))
        job.pop('evidence', None)  # Relevant source quotes are retained in checks and raw JD.
        analysis = job.get('analysis')
        if isinstance(analysis, dict):
            job['analysis'] = {
                '核对概览': analysis.get('summary', ''),
                '逐项核对': [{'条件': c.get('label', ''), '结论': VALUE_LABELS.get(c.get('status'), c.get('status')),
                              '重要性': VALUE_LABELS.get(c.get('priority'), c.get('priority')),
                              '说明与引用': c.get('evidence', '') if c.get('key') in {'role', 'skills'}
                                            else _reason(c.get('evidence', ''))} for c in analysis.get('checks', [])],
                '待核对问题': [_reason(q) for q in analysis.get('questions', [])],
            }
    return result
