"""One explicitly requested, low-volume real BOSS collection; never sends messages."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.models import Profile
from app.providers import collect_source


def main():
    events = []
    def emit(value):
        events.append({'stage': value.get('stage'), 'records': len(value.get('jobs', []))})
    profile = Profile(city='郑州', cities=['郑州'], target_roles=['客服'])
    result = collect_source('boss', profile, {'keyword': '客服', 'city': '郑州', 'max_list': 5, 'max_details': 1}, emit, lambda: False)
    jobs = [j.model_dump() if hasattr(j, 'model_dump') else j for j in result.get('jobs', [])]
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'source': 'boss',
              'status': result['status'], 'count': len(jobs),
              'full_jd_count': sum(j.get('completeness') == 'full_jd' for j in jobs),
              'warnings': result.get('warnings', []), 'diagnostics': result.get('diagnostics', {}),
              'events': events, 'jobs': jobs}
    target = ROOT / 'test-results'
    target.mkdir(exist_ok=True)
    (target / 'boss-v1-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('jobs', 'events')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
