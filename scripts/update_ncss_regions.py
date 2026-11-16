"""Generate a public region catalog from NCSS's own published page asset."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://t4.chei.com.cn/ncss/student/js/datas-1.0.2.js'


def main():
    response = httpx.get(URL, timeout=15, trust_env=False)
    response.raise_for_status()
    rows = re.findall(r'\{\s*value\s*:\s*"(\d{2}|\d{4}|\d{6})"\s*,\s*name\s*:\s*"([^"]+)"\s*\}', response.text)
    grouped = {}
    for code, name in rows:
        if len(code) == 2 or code.endswith('00'):
            grouped.setdefault(name, set()).add(code)
    regions = {name: next(iter(codes)) for name, codes in grouped.items() if len(codes) == 1}
    for name, codes in grouped.items():
        province = [code for code in codes if len(code) == 2]
        if len(province) == 1:
            regions[name] = province[0]
    if regions.get('郑州市') != '410100' or len(regions) < 200:
        raise RuntimeError('Official region asset did not match the validated schema; no catalog written')
    payload = {'source': URL, 'generated_at': datetime.now(timezone.utc).isoformat(), 'regions': regions}
    output = ROOT / 'app' / 'ncss_regions.json'
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Generated {len(regions)} public region mappings from the official NCSS asset.')


if __name__ == '__main__':
    main()
