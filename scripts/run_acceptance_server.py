"""Run the real UI in isolated data without overwriting the owner's profile.

Uses the already-authorized model credentials only in child process memory.
Touch test-results/acceptance-server-18765.stop to stop this owned child.
No credentials or database contents are printed, copied, or packaged.
"""
from pathlib import Path
import os
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.model_config import get_credentials

PORT = 18765


def main():
    with socket.socket() as check:
        check.bind(('127.0.0.1', PORT))
    credentials = get_credentials()
    output = ROOT / 'test-results'
    output.mkdir(exist_ok=True)
    stop = output / f'acceptance-server-{PORT}.stop'
    stop.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix='fanwan-acceptance-') as directory:
        env = os.environ | {'JOB_DEMO_DATA_DIR': directory,
                            'JOB_LLM_API_KEY': credentials['api_key'],
                            'JOB_LLM_BASE_URL': credentials['base_url'],
                            'JOB_LLM_MODEL': credentials['model'],
                            'PYTHONIOENCODING': 'utf-8'}
        code = ("import os; from pathlib import Path; from app import model_config; "
                "assert model_config._directory().resolve() == Path(os.environ['JOB_DEMO_DATA_DIR']).resolve(); "
                "import uvicorn; uvicorn.run('app.main:app',host='127.0.0.1',port=18765,log_level='warning')")
        process = subprocess.Popen([sys.executable, '-c', code], cwd=ROOT, env=env)
        print(f'Isolated real-UI acceptance: http://127.0.0.1:{PORT}/', flush=True)
        print('Only temporary test profile/history/results are written; original local data stays untouched.', flush=True)
        deadline = time.monotonic() + 1800
        try:
            while process.poll() is None and not stop.exists() and time.monotonic() < deadline:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            stop.unlink(missing_ok=True)
    print('Acceptance server stopped; temporary data removed.', flush=True)


if __name__ == '__main__':
    main()
