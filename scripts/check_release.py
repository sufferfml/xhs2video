"""Check candidate source, commit metadata, history and build archives for privacy leaks.

This supplements Gitleaks. Findings display rule names and file paths, never values.
"""
import argparse
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile

RULES = {
    'personal-home-path': re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
    'windows-home-path': re.compile(r'[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+\\'),
    'numeric-chat-identifier': re.compile(r'(?i)(?:sender_id|chat_id|chatId|owner|recipient|sender)[^\n]{0,60}?\b\d{7,15}\b'),
    'signed-url': re.compile(r'(?i)[?&](?:xsec_token|access_token|api_key)=(?!EXAMPLE_)[A-Za-z0-9_%+/-]{16,}'),
}
BANNED_PARTS = {'.git', '.venv', '__pycache__', 'browser-profile', 'private'}
BANNED_SUFFIXES = {'.mp3', '.mp4', '.wav', '.m4a', '.pem', '.key', '.bundle', '.log'}
findings = []


def inspect(name: str, content: bytes) -> None:
    path = Path(name)
    if (set(path.parts) & BANNED_PARTS or path.suffix.lower() in BANNED_SUFFIXES
            or path.name == '.env' or path.name.startswith(('cookies', 'storage_state'))):
        findings.append((name, 'unexpected-private-artifact'))
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        return
    for rule, pattern in RULES.items():
        if pattern.search(text):
            findings.append((name, rule))


def git(*args: str) -> bytes:
    return subprocess.check_output(['git', *args])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true')
    parser.add_argument('--archive', type=Path, action='append', default=[])
    args = parser.parse_args()
    if not args.archive:
        paths = git('ls-files', '--cached', '--others', '--exclude-standard', '-z').decode().split('\0')
        for name in paths:
            if name and Path(name).is_file():
                inspect(name, Path(name).read_bytes())
    if args.history:
        for line in git('rev-list', '--objects', '--all').decode().splitlines():
            parts = line.split(' ', 1)
            if len(parts) == 2 and git('cat-file', '-t', parts[0]).strip() == b'blob':
                inspect(parts[1], git('cat-file', 'blob', parts[0]))
        for line in git('log', '--all', '--format=%ae%n%ce').decode().splitlines():
            if not (line.endswith('@users.noreply.github.com') or line == 'noreply@github.com'):
                findings.append(('commit-metadata', 'non-noreply-email'))
        inspect('commit-messages', git('log', '--all', '--format=%B'))
    for archive in args.archive:
        if archive.suffix == '.whl' or archive.suffix == '.zip':
            with zipfile.ZipFile(archive) as container:
                for name in container.namelist():
                    if not name.endswith('/'):
                        inspect(name, container.read(name))
        else:
            with tarfile.open(archive) as container:
                for member in container.getmembers():
                    if member.isfile():
                        inspect(member.name, container.extractfile(member).read())
    if findings:
        for name, rule in sorted(set(findings)):
            print(f'FAIL {rule}: {name}')
        raise SystemExit(1)
    print('PASS: source/privacy/artifact checks (supplement with Gitleaks)')


if __name__ == '__main__':
    main()
