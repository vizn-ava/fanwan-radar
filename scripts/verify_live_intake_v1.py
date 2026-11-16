"""Exercise actual paragraph interpretation with explicitly synthetic test goals.

No production profile, chat history or job results are modified. Credentials are
loaded only by the backend helper; reports contain no Key or raw model payload.
"""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.models import Profile
from app.profile_intake import interpret


async def main():
    profile = Profile()
    text = ('我想在郑州找客服。我是本科，有3年客服经验，会Excel。希望月薪6000到9000元，'
            '不要求固定周末，但每周要休两天，要五险一金。不接受夜班或倒班，最多每天8小时。'
            '工资口径没定，先不要按固定底薪理解。')
    started = time.perf_counter()
    first = await interpret({'profile': profile, 'revision': 0, 'generation': 0, 'messages': []}, text, {})
    candidate = Profile.model_validate(profile.model_dump() | first['patch'], strict=True)
    checks = {'city': candidate.city == '郑州' or candidate.cities == ['郑州'],
              'role': candidate.target_roles == ['客服'], 'salary_range': (candidate.salary_min, candidate.salary_max) == (6000, 9000),
              'not_invented_base': candidate.salary_basis == 'unspecified',
              'weekly_not_weekend': any(s.kind == 'weekly_double' and not s.fixed_weekdays for s in candidate.accepted_schedules)
                                    and not any(s.kind == 'weekend_double' for s in candidate.accepted_schedules),
              'insurance': candidate.insurance_requirement == 'five_and_fund',
              'no_night_or_rotation': candidate.accept_night_shift is False and candidate.accept_rotating_shift is False,
              'hours': candidate.max_daily_hours == 8,
              'education_experience_explicit': candidate.education == '本科' and candidate.experience_years == 3,
              'skill': 'Excel' in candidate.skills, 'no_invented_name': not candidate.name}
    first_ms = round((time.perf_counter()-started)*1000)
    second = await interpret({'profile': candidate, 'revision': 1, 'generation': 0, 'messages': []},
                             '再补充一项技能Python。其他条件都不变。', {})
    updated = Profile.model_validate(candidate.model_dump() | second['patch'], strict=True)
    checks['additive_skill_preserves_previous'] = {'Excel', 'Python'} <= set(updated.skills)
    checks['unmentioned_conditions_preserved'] = (updated.city, updated.salary_min, updated.salary_max,
                                                 updated.accept_night_shift, updated.accepted_schedules) == (
                                                 candidate.city, candidate.salary_min, candidate.salary_max,
                                                 candidate.accept_night_shift, candidate.accepted_schedules)
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'actual_model_call': True,
              'synthetic_profile_only': True, 'first_interpret_ms': first_ms,
              'total_ms': round((time.perf_counter()-started)*1000), 'checks': checks,
              'all_passed': all(checks.values()), 'production_state_written': False}
    directory = ROOT / 'test-results'
    directory.mkdir(exist_ok=True)
    (directory / 'intake-live-v1.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['all_passed']:
        raise RuntimeError('Actual model extraction needs iteration; failed checks are shown without private data.')


if __name__ == '__main__':
    asyncio.run(main())
