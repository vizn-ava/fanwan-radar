"""Version local frontend assets without caching a stale application shell."""
import re

_ASSET_ATTRIBUTE = re.compile(r'((?:src|href)=["\'])(/static/[^"\']+)(["\'])', re.I)


def render_index(source: str, build_id: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', build_id):
        raise ValueError('Invalid frontend build identity')
    source = source.replace('__BUILD_ID__', build_id)

    def version(match):
        path = match[2].split('?', 1)[0]
        if not path.lower().endswith(('.js', '.css')):
            return match[0]
        return match[1] + path + '?v=' + build_id + match[3]

    return _ASSET_ATTRIBUTE.sub(version, source)
