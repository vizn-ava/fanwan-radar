"""One bounded public-source verification, distinct from synthetic unit tests."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.models import Profile
from app.providers import collect_source


def main():
    result = collect_source('ncss', Profile(city='郑州', target_roles=['客服']),
                            {'city': '郑州', 'keyword': '客服', 'max_list': 5, 'max_details': 2},
                            lambda event: None, lambda: False)
    jobs = [j.model_dump() if hasattr(j, 'model_dump') else j for j in result.get('jobs', [])]
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'source': 'ncss',
              'status': result['status'], 'count': len(jobs),
              'full_jd_count': sum(j.get('completeness') == 'full_jd' for j in jobs),
              'warnings': result.get('warnings', []), 'diagnostics': result.get('diagnostics', {}), 'jobs': jobs}
    directory = ROOT / 'test-results'
    directory.mkdir(exist_ok=True)
    (directory / 'ncss-v1-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'jobs'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
