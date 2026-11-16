"""Check the actual deliverable, not only the working directory."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from app import model_config
    known_key = (model_config.get_credentials(require_model=False)['api_key'].encode()
                 if model_config.public_settings()['has_key'] else None)
    bundle = ROOT / 'dist' / 'fanwan-radar-v1.zip'
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        for name in names:
            parts = Path(name).parts
            if (not name.startswith('fanwan-radar-v1/') or '..' in parts
                    or name.startswith(('/', '\\')) or ':' in parts[0]):
                raise RuntimeError('Unsafe archive path')
            if any(x in parts for x in ['.venv', 'data', 'test-results', '.git']) and '/third_party/' not in name:
                raise RuntimeError('Forbidden local state included: ' + name)
            if name.endswith(('.sqlite3', '.sqlite3-wal', '.sqlite3-shm', '.db', '.dpapi', '/.env')):
                raise RuntimeError('Private file included: ' + name)
            if known_key and known_key in archive.read(name):
                raise RuntimeError('Credential detected in deliverable; verification stopped without printing it.')
        if not any(name.endswith('/boss-zhipin-scraper/LICENSE') for name in names):
            raise RuntimeError('Upstream attribution missing')
        if not any(name.endswith('/vendor/marked/LICENSE') for name in names):
            raise RuntimeError('Markdown lexer license missing')
        with tempfile.TemporaryDirectory(prefix='fanwan-bundle-check-') as temporary:
            archive.extractall(temporary)
            unpacked = Path(temporary) / 'fanwan-radar-v1'
            env = {k: v for k, v in os.environ.items() if not k.startswith('JOB_')}
            env |= {'PYTHONIOENCODING': 'utf-8', 'PYTHONPATH': '',
                    'JOB_DEMO_DATA_DIR': str(Path(temporary) / 'isolated-test-data')}
            subprocess.run([sys.executable, '-m', 'pytest', '-q'], cwd=unpacked, env=env, check=True)
            subprocess.run(['node', '--check', str(unpacked / 'app/static/app.js')], check=True)
            renderer = unpacked / 'app/static/markdown.js'
            if renderer.is_file():
                subprocess.run(['node', '--check', str(renderer)], check=True)
    print('Bundle verified: fresh-extraction tests/JS pass; no local state, DPAPI files or known Key included.')


if __name__ == '__main__':
    main()
