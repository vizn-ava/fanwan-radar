"""Fence the entire pytest process away from the owner's profile and Key.

This runs before test-module imports. Individual tests may use narrower temp
directories, but teardown/background work always falls back to this disposable
session area, never the real data directory. The environment is process-local.
"""
import os
import tempfile

_session_data = tempfile.TemporaryDirectory(prefix='fanwan-pytest-session-')
os.environ['JOB_DEMO_DATA_DIR'] = _session_data.name
for _name in ('JOB_LLM_API_KEY', 'JOB_LLM_BASE_URL', 'JOB_LLM_MODEL', 'JOB_ENABLE_RESUME_CONTEXT'):
    os.environ.pop(_name, None)
