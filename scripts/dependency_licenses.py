"""Generate a lock-derived license inventory from version-specific PyPI metadata.

Requires Python 3.11+ and curl. Does not run during application use.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import tomllib


def inspect(package):
    name, version = package['name'], package['version']
    url = f'https://pypi.org/pypi/{name}/{version}/json'
    result = subprocess.run(['curl', '-fsSL', '--retry', '2', '--max-time', '20', url],
                            capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)['info']
    expression = info.get('license_expression')
    classifiers = [c for c in info.get('classifiers', []) if c.startswith('License ::')]
    declared = info.get('license') or ''
    # Some metadata includes an entire license; preserve it without losing attribution.
    return {'name': name, 'version': version, 'license_expression': expression,
            'license_classifiers': classifiers, 'declared_license': declared,
            'source': url}


if __name__ == '__main__':
    with open('uv.lock', 'rb') as stream:
        packages = tomllib.load(stream)['package']
    packages = [p for p in packages if 'registry' in p.get('source', {})]
    with ThreadPoolExecutor(max_workers=6) as pool:
        inventory = list(pool.map(inspect, packages))
    Path('docs/dependency-licenses.json').write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + '\n')
    print(f'Recorded version-specific metadata for {len(inventory)} locked dependency entries')
