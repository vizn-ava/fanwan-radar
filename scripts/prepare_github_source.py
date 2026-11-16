"""Prepare a clean publishing tree from the verified source bundle.

The development directory contains private runtime state and a nested upstream
checkout, so it is never added wholesale to a new public Git repository.
"""
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    destination = ROOT / 'dist/github-source'
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError('Publishing directory already contains files; refusing to overwrite it.')
    subprocess.run([sys.executable, str(ROOT / 'scripts/verify_bundle.py')], cwd=ROOT, check=True)
    with zipfile.ZipFile(ROOT / 'dist/fanwan-radar-v1.zip') as archive:
        for name in archive.namelist():
            if not name.startswith('fanwan-radar-v1/') or '..' in Path(name).parts or '\\' in name:
                raise RuntimeError('Unsafe source archive path')
        destination.mkdir(parents=True, exist_ok=True)
        archive.extractall(destination)
    print('CLEAN_GITHUB_SOURCE=' + str(destination / 'fanwan-radar-v1'))


if __name__ == '__main__':
    main()
