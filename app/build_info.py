"""Identify loaded application code without reading local user data."""
import hashlib
from pathlib import Path


def code_build_id(directory: Path | None = None) -> str:
    root = directory or Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_file() and (path.suffix in {'.py', '.html', '.css', '.js'} or path.name == 'ncss_regions.json'):
            digest.update(path.relative_to(root).as_posix().encode('utf-8'))
            digest.update(b'\x00')
            digest.update(path.read_bytes())
            digest.update(b'\x00')
    return digest.hexdigest()[:16]
