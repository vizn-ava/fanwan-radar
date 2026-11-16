"""Install the verified official lexer artifact; runtime needs no npm or CDN.

Maintainer preparation: npm pack marked@18.2.0 --pack-destination third_party
Only fixed regular archive members are copied, never arbitrary archive paths.
"""
import base64
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '18.2.0'
INTEGRITY = 'sha512-KaeL+tAbtvI930q7HE2PN5lYXYdT8EiSmxHlN6F3rsfb57wd5mp5W5TLazpdj4inTQBJdgjYwbs40W/0VUGU+Q=='


def main():
    archive = ROOT / 'third_party' / f'marked-{VERSION}.tgz'
    digest = 'sha512-' + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
    if digest != INTEGRITY:
        raise RuntimeError('The npm artifact does not match its verified integrity.')
    output = ROOT / 'app/static/vendor/marked'
    output.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:gz') as package:
        metadata = json.load(package.extractfile('package/package.json'))
        if metadata['name'] != 'marked' or metadata['version'] != VERSION or metadata['license'] != 'MIT':
            raise RuntimeError('Unexpected npm package metadata.')
        for source, name in [('package/lib/marked.umd.js', 'marked.umd.js'),
                             ('package/lib/marked.umd.js.map', 'marked.umd.js.map'), ('package/LICENSE', 'LICENSE')]:
            member = package.getmember(source)
            if not member.isfile() or member.size > 300000:
                raise RuntimeError('Unexpected archive member.')
            (output / name).write_bytes(package.extractfile(member).read())
    notice = {'name': 'marked', 'version': VERSION, 'license': 'MIT', 'integrity': INTEGRITY,
              'source': f'https://registry.npmjs.org/marked/-/marked-{VERSION}.tgz',
              'runtime': 'Local lexer only; generated HTML is not executed or inserted.',
              'sha256': hashlib.sha256((output / 'marked.umd.js').read_bytes()).hexdigest()}
    (output / 'NOTICE.json').write_text(json.dumps(notice, indent=2) + '\n', encoding='utf-8')
    print(f'Vendored marked {VERSION}: verified npm integrity, lexer and unmodified MIT license.')


if __name__ == '__main__':
    main()
