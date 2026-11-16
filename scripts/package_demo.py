"""Package an explicit source allowlist; never include user data or browser state."""
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    required = ['app/main.py', 'app/static/index.html', 'app/static/app.js',
                'app/static/styles.css', 'app/static/vendor/marked/marked.umd.js',
                'app/static/vendor/marked/LICENSE', 'third_party/boss-zhipin-scraper/LICENSE']
    for name in required:
        if not (ROOT / name).is_file():
            raise RuntimeError('Refusing to package an incomplete app: ' + name)
    output_dir = ROOT / 'dist'
    output_dir.mkdir(exist_ok=True)
    output = output_dir / 'fanwan-radar-v1.zip'
    files = []
    for name in ['README.md', 'CONTRACT.md', 'VERIFICATION.md', 'V1_REVIEW.md', 'requirements.txt',
                 'pytest.ini', '.gitignore', '.gitattributes', '.env.example', 'start.ps1',
                 'start-demo.cmd', '打开BOSS登录.ps1']:
        p = ROOT / name
        if p.is_file():
            files.append(p)
    obsolete = {'app/fixtures.py', 'scripts/check_live_sources.py'}
    for folder in ['app', 'tests', 'scripts']:
        for p in (ROOT / folder).rglob('*'):
            relative = p.relative_to(ROOT).as_posix()
            if (p.is_file() and '__pycache__' not in p.parts and relative not in obsolete
                    and p.suffix in {'.py', '.html', '.css', '.js', '.txt', '.json'}
                    and p.name not in {'credentials.json', 'settings.local.json'}):
                files.append(p)
    for name in ['app/static/vendor/marked/LICENSE', 'app/static/vendor/marked/marked.umd.js.map',
                 'third_party/README.md', 'third_party/boss-zhipin-scraper/LICENSE',
                 'third_party/boss-zhipin-scraper/README.md',
                 'third_party/boss-zhipin-scraper/scripts/boss_cdp_raw.py',
                 'third_party/boss-zhipin-scraper/scripts/job_summary.py',
                 'third_party/boss-zhipin-scraper/data/city_codes.json']:
        p = ROOT / name
        if p.is_file():
            files.append(p)
    upstream = ROOT / 'third_party/boss-zhipin-scraper'
    try:
        result = subprocess.run(['git', '-C', str(upstream), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    except FileNotFoundError:
        result = None
    if result and result.returncode == 0:
        commit = result.stdout.strip()
    else:
        metadata = ROOT / 'UPSTREAM.txt'
        lines = metadata.read_text(encoding='utf-8').splitlines() if metadata.exists() else []
        commit = next((line.removeprefix('Commit: ').strip() for line in lines if line.startswith('Commit: ')), 'unknown')
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(set(files)):
            archive.write(p, 'fanwan-radar-v1/' + p.relative_to(ROOT).as_posix())
        archive.writestr('fanwan-radar-v1/UPSTREAM.txt',
                         f'https://github.com/eatmoreduck/boss-zhipin-scraper\nCommit: {commit}\nLicense: MIT\n')
    print(f'{output}\n{len(set(files))} source files; {output.stat().st_size} bytes; no user data or virtual environment')


if __name__ == '__main__':
    main()
