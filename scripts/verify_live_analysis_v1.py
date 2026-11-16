"""Verify actual model deltas against captured real jobs without editing user data."""
import asyncio
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.analysis_stream import stream_reply
from app.model_config import get_credentials
from app.models import Job, Profile, Schedule
from app.matching import analyze


async def main():
    source = json.loads((ROOT / 'test-results/boss-v1-verification.json').read_text(encoding='utf-8'))
    jobs = [Job.model_validate(j) for j in source['jobs'] if j.get('completeness') == 'full_jd'][:1]
    if not jobs:
        raise RuntimeError('No captured full real JD; this check will not substitute a fabricated vacancy.')
    profile = Profile(city='郑州', cities=['郑州'], target_roles=['客服'], salary_min=6000,
                      salary_basis='unspecified', insurance_requirement='five_and_fund',
                      accepted_schedules=[Schedule(kind='weekly_double')])
    context = [j.model_dump() | {'analysis': analyze(j, profile)} for j in jobs]
    snapshot = {'profile': profile, 'revision': 0, 'messages': []}
    credentials = get_credentials()
    events, deltas = [], []
    started = time.perf_counter()

    def observe(name):
        events.append((name, round((time.perf_counter() - started) * 1000)))

    schedule_probe = '--schedule-check' in sys.argv
    question = ('大小周能满足我每周休两天的要求吗？请结合这份岗位的标题与正文，简短说明依据和待确认项，不要修改条件或搜索。'
                if schedule_probe else '请分析这份真实岗位与已保存条件的关系，说明工资、五险一金和休息安排哪些有依据、哪些需要问清楚。不要修改条件、搜索或投递。')
    async for delta in stream_reply(credentials, snapshot, question,
                                   context, observer=observe):
        deltas.append(delta)
    reply = ''.join(deltas)
    if not reply or credentials['api_key'] in reply:
        raise RuntimeError('Live stream verification failed; no credential content will be printed.')
    if schedule_probe and not ('大小周' in reply and '单休' in reply and re.search(r'不能|无法|不满足|不保证|不符合', reply)):
        raise RuntimeError('Schedule answer was not explicit enough; this probe is not marked passed.')
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'model': credentials['model'],
              'real_job_count': len(jobs), 'delta_count': len(deltas), 'characters': len(reply),
              'first_delta_ms': next((ms for name, ms in events if name == 'delta'), None),
              'reasoning_event_count': sum(name == 'reasoning' for name, _ in events),
              'total_ms': round((time.perf_counter()-started)*1000), 'completed': True,
              'profile_or_history_modified': False, 'schedule_rule_probe': schedule_probe, 'reply': reply}
    report_path = ROOT / ('test-results/schedule-analysis-live-v1.json' if schedule_probe else 'test-results/live-analysis-v1.json')
    if report_path.exists():
        previous = json.loads(report_path.read_text(encoding='utf-8'))
        if previous.get('reasoning_event_count'):
            (ROOT / 'test-results/live-analysis-before-v1.json').write_text(
                json.dumps(previous, ensure_ascii=False, indent=2), encoding='utf-8')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'reply'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
