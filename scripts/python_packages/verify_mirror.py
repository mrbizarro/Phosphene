#!/usr/bin/env python3
"""Check each locked macOS wheel's name/hash and availability on both candidates.
Read-only audit; installs still enforce the PyPI hashes on the downloaded bytes.
Run with a Python containing packaging, after regenerate.py, with --output PATH.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
from packaging.tags import cpython_tags, compatible_tags, mac_platforms
from packaging.utils import parse_wheel_filename

HERE = Path(__file__).resolve().parent
MIRRORS = {'aliyun': 'https://mirrors.aliyun.com/pypi/simple/',
           'tsinghua': 'https://pypi.tuna.tsinghua.edu.cn/simple/'}


class Links(HTMLParser):
    def __init__(self, body):
        super().__init__(); self.links = {}; self.feed(body)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('href'):
            href = attrs['href']
            name = urllib.parse.unquote(urllib.parse.urlsplit(href).path.rsplit('/', 1)[-1])
            self.links[name] = href


def read(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def check(item):
    (name, version), want = item
    result = {'package': name, 'version': version, 'wheels': [], 'mirrors': {}}
    try:
        release = json.loads(read('https://pypi.org/pypi/' + name + '/' + version + '/json'))
        chosen = {}
        for py in sorted(want):
            platforms = list(mac_platforms((14, 0), 'arm64'))
            pair = tuple(map(int, py.split('.')))
            tags = list(cpython_tags(pair, platforms=platforms)) + list(compatible_tags(pair, platforms=platforms))
            ranks = {tag: i for i, tag in enumerate(tags)}
            candidates = []
            for file in release['urls']:
                if not file['filename'].endswith('.whl'):
                    continue
                wheel_tags = parse_wheel_filename(file['filename'])[3]
                rank = min((ranks[t] for t in wheel_tags if t in ranks), default=None)
                if rank is not None and file['digests']['sha256'] in want[py]:
                    candidates.append((rank, file['filename'], file['digests']['sha256']))
            if not candidates:
                raise ValueError('No PyPI wheel/hash for Python ' + py)
            _, filename, sha = min(candidates)
            chosen[filename] = sha
        result['wheels'] = [{'filename': f, 'sha256': h} for f, h in chosen.items()]
        for mirror, base in MIRRORS.items():
            try:
                links = Links(read(base + name + '/').decode()).links
                for filename, sha in chosen.items():
                    url = urllib.parse.urljoin(base + name + '/', links[filename])
                    assert urllib.parse.parse_qs(urllib.parse.urlsplit(url).fragment).get('sha256') == [sha], 'Advertised hash differs'
                    with urllib.request.urlopen(urllib.request.Request(url.split('#')[0], method='HEAD'), timeout=30) as response:
                        assert response.status == 200
                result['mirrors'][mirror] = 'matching PyPI hashes; wheel HEAD 200'
            except Exception as exc:
                result['mirrors'][mirror] = type(exc).__name__ + ': ' + str(exc)
    except Exception as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    entries = json.loads((HERE / 'sets.json').read_text())
    work = {}
    for name, entry in entries.items():
        if entry.get('pypi_only'):
            continue
        text = (HERE / (name + '.txt')).read_text()
        for match in re.finditer(r'^([a-zA-Z0-9_.-]+)==([^\s;]+)(.*?)(?=^[a-zA-Z]|\Z)', text, re.M | re.S):
            pkg, version, tail = match.groups()
            key = (re.sub(r'[-_.]+', '-', pkg).lower(), version)
            work.setdefault(key, {}).setdefault(entry['python'], set()).update(re.findall(r'--hash=sha256:([0-9a-f]{64})', tail))
    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(check, sorted(work.items())):
            results.append(result)
            print(result['package'] + ' ' + result['version'] + ': ' + str(result.get('error', result['mirrors'])), flush=True)
            Path(args.output).write_text(json.dumps({'platform': 'macOS 14 arm64', 'results': results}, indent=2) + '\n')


if __name__ == '__main__':
    main()
