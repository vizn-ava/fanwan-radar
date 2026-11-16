"""User-owned model configuration. Secrets never enter public settings or SQLite."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit, urlunsplit

from . import storage

_lock = threading.RLock()
_session_keys: dict[str, str] = {}
_PREFIX = b'fanwan-radar-model-key-v2\x00'


class SettingsError(ValueError):
    def __init__(self, message: str, status_code: int = 422, code: str = 'invalid_settings'):
        super().__init__(message)
        self.status_code, self.code = status_code, code


def _directory() -> Path:
    return Path(os.getenv('JOB_DEMO_DATA_DIR', str(Path(__file__).resolve().parents[1] / 'data')))


def _secret_file() -> Path:
    return _directory() / 'model-key.dpapi'


class _Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(content: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != 'nt':
        raise SettingsError('当前系统使用会话内密钥；重启后需要重新填写', 422, 'session_only')
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(content)).from_buffer_copy(content)
    incoming = _Blob(len(content), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = _Blob()
    if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise SettingsError('系统密钥保护失败；密钥未保存，请检查当前 Windows 用户权限', 500, 'key_protection_failed')
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        kernel.LocalFree(outgoing.pbData)


def _settings() -> dict:
    value = storage.get('model_settings', {})
    return value if isinstance(value, dict) else {}


def _load_key(settings: dict) -> tuple[str, str]:
    path = _secret_file()
    if os.name == 'nt' and path.is_file():
        try:
            data = _dpapi(path.read_bytes(), decrypt=True)
            if data.startswith(_PREFIX):
                return data[len(_PREFIX):].decode('utf-8'), 'windows_dpapi'
        except (OSError, UnicodeError, SettingsError):
            return '', 'unreadable'
    key = _session_keys.get(str(_directory().resolve()), '')
    if key:
        return key, 'session'
    if not settings.get('ignore_env_key') and os.getenv('JOB_LLM_API_KEY'):
        return os.environ['JOB_LLM_API_KEY'], 'environment'
    return '', 'none'


def public_settings() -> dict:
    with _lock:
        settings = _settings()
        key, mechanism = _load_key(settings)
        base = settings.get('base_url', os.getenv('JOB_LLM_BASE_URL', 'https://api.openai.com/v1'))
        model = settings.get('model', os.getenv('JOB_LLM_MODEL', ''))
        return {'base_url': base, 'model': model, 'has_key': bool(key),
                'key_storage': mechanism, 'configured': bool(key and base and model),
                'warning': '密钥仅保留在本次服务进程中，重启后需重填' if mechanism == 'session' else
                           '已保存的密钥无法由当前系统用户解密，请重新填写' if mechanism == 'unreadable' else ''}


def _validate_base(base: str) -> str:
    try:
        parts = urlsplit(base.strip())
        if parts.scheme not in ('https', 'http') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError()
        _ = parts.port
    except ValueError as exc:
        raise SettingsError('服务地址需为完整 http/https 地址，不能带账号、密钥参数或片段') from exc
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/'), '', ''))


def save_settings(payload: dict) -> dict:
    with _lock:
        previous = public_settings()
        base = payload.get('base_url', previous['base_url'])
        model = payload.get('model', previous['model'])
        key = payload.get('api_key', '')
        if not all(isinstance(x, str) for x in (base, model, key)):
            raise SettingsError('地址、模型和 Key 必须是文本')
        base = _validate_base(base)
        model, key = model.strip(), key.strip()
        if len(model) > 200:
            raise SettingsError('模型名称最长 200 字符')
        if len(base) > 2000 or len(key) > 4096 or any(c in key for c in ('\r', '\n', '\x00')):
            raise SettingsError('服务地址或 Key 格式不正确')
        settings = _settings() | {'base_url': base, 'model': model}
        if key:
            if os.name == 'nt':
                protected = _dpapi(_PREFIX + key.encode('utf-8'))
                path = _secret_file()
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.tmp')
                try:
                    temporary.write_bytes(protected)
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            else:
                _session_keys[str(_directory().resolve())] = key
            settings['ignore_env_key'] = True
        storage.put('model_settings', settings)
        return public_settings()


def remove_key() -> dict:
    with _lock:
        _secret_file().unlink(missing_ok=True)
        _session_keys.pop(str(_directory().resolve()), None)
        storage.put('model_settings', _settings() | {'ignore_env_key': True})
        return public_settings()


def get_credentials(*, require_model: bool = True) -> dict:
    with _lock:
        settings = public_settings()
        key, _ = _load_key(_settings())
        if not key or not settings['base_url'] or (require_model and not settings['model']):
            raise SettingsError('请先在模型设置中填写自己的 API Key、服务地址和模型，并测试连接', 428, 'model_not_configured')
        return {'base_url': settings['base_url'], 'model': settings['model'], 'api_key': key}
