"""Create a private local SQLite backup before a schema/application upgrade."""
from datetime import datetime
import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


def main():
    directory = Path(os.getenv('JOB_DEMO_DATA_DIR', str(ROOT / 'data')))
    path = directory / 'local.sqlite3'
    if not path.exists():
        print('No previous local database; no backup needed.')
        return
    target_dir = directory / 'backups'
    target_dir.mkdir(exist_ok=True)
    target = target_dir / ('before-v2-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite3')
    source = sqlite3.connect(path)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    print('Created private local backup in data/backups; no record contents printed or exported.')


if __name__ == '__main__':
    main()
