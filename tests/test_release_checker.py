"""Exercise advisory email checks without weakening blocking privacy checks."""
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'check_release.py'
NOREPLY = 'test-contributor@users.noreply.github.com'
PUBLIC_EMAIL = 'contributor@example.invalid'


def init_repo(path, author_email=NOREPLY, committer_email='noreply@github.com'):
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'test-contributor',
           'GIT_AUTHOR_EMAIL': author_email,
           'GIT_COMMITTER_NAME': 'test-committer',
           'GIT_COMMITTER_EMAIL': committer_email}

    def git(*args):
        subprocess.run(['git', '-c', 'commit.gpgsign=false', *args],
                       cwd=path, env=env, check=True, capture_output=True)

    git('init', '-b', 'main')
    (path / 'README.md').write_text('Generated fixture; no personal data.')
    git('add', 'README.md')
    git('commit', '-m', 'Fixture')
    return git


def check(path, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          cwd=path, capture_output=True, text=True)


@pytest.mark.parametrize('author_email,committer_email,warns', [
    (NOREPLY, 'noreply@github.com', False),
    (NOREPLY, NOREPLY, False),
    (PUBLIC_EMAIL, NOREPLY, True),
    (NOREPLY, PUBLIC_EMAIL, True),
    (PUBLIC_EMAIL, PUBLIC_EMAIL, True),
])
def test_release_metadata_policy(tmp_path, author_email, committer_email, warns):
    init_repo(tmp_path, author_email, committer_email)
    result = check(tmp_path, '--history')
    assert result.returncode == 0
    assert result.stdout.count('WARN non-noreply-email: commit-metadata') == int(warns)
    assert 'PASS: blocking' in result.stdout
    for email in (author_email, committer_email):
        assert email not in result.stdout + result.stderr


def signed_url():
    token = 'generated-fixture-' * 3
    return f'https://example.invalid/?access_token={token}'


@pytest.mark.parametrize('name,content,rule', [
    ('note.txt', signed_url(), 'signed-url'),
    ('note.txt', '/' + 'Users' + '/fixture-person/document', 'personal-home-path'),
    ('note.txt', 'chat_id: ' + '123' * 3, 'numeric-chat-identifier'),
    ('.env', 'GENERATED_FIXTURE_ONLY=not-a-real-secret', 'unexpected-private-artifact'),
    ('fixture.pem', 'generated-key-fixture-only', 'unexpected-private-artifact'),
    ('cookies.json', '{"fixture": true}', 'unexpected-private-artifact'),
    ('browser-profile/state.json', '{"fixture": true}', 'unexpected-private-artifact'),
])
def test_email_warning_does_not_hide_blocking_findings(tmp_path, name, content, rule):
    init_repo(tmp_path, committer_email=PUBLIC_EMAIL)
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    result = check(tmp_path, '--history')
    assert result.returncode == 1
    assert 'WARN non-noreply-email' in result.stdout
    assert f'FAIL {rule}: {name}' in result.stdout
    assert 'PASS:' not in result.stdout
    for value in (PUBLIC_EMAIL, content):
        assert value not in result.stdout + result.stderr


def test_sensitive_content_still_blocks_after_deletion_from_working_tree(tmp_path):
    git = init_repo(tmp_path, committer_email=PUBLIC_EMAIL)
    content = signed_url()
    (tmp_path / 'note.txt').write_text(content)
    git('add', 'note.txt')
    git('commit', '-m', 'Generated history fixture')
    git('rm', 'note.txt')
    git('commit', '-m', 'Remove generated fixture')
    assert check(tmp_path).returncode == 0
    result = check(tmp_path, '--history')
    assert result.returncode == 1
    assert 'FAIL signed-url: note.txt' in result.stdout
    assert content not in result.stdout + result.stderr


def test_sensitive_commit_message_still_blocks(tmp_path):
    git = init_repo(tmp_path, committer_email=PUBLIC_EMAIL)
    content = signed_url()
    git('commit', '--allow-empty', '-m', content)
    result = check(tmp_path, '--history')
    assert result.returncode == 1
    assert 'FAIL signed-url: commit-messages' in result.stdout
    assert content not in result.stdout + result.stderr


@pytest.mark.parametrize('kind', ['whl', 'tar.gz'])
def test_private_archive_artifact_still_blocks(tmp_path, kind):
    archive = tmp_path / f'fixture.{kind}'
    content = b'GENERATED_FIXTURE_ONLY=not-a-real-secret'
    if kind == 'whl':
        with zipfile.ZipFile(archive, 'w') as container:
            container.writestr('package/.env', content)
    else:
        with tarfile.open(archive, 'w:gz') as container:
            member = tarfile.TarInfo('package/.env')
            member.size = len(content)
            container.addfile(member, BytesIO(content))
    result = check(tmp_path, '--archive', archive)
    assert result.returncode == 1
    assert 'FAIL unexpected-private-artifact: package/.env' in result.stdout
    assert content.decode() not in result.stdout + result.stderr
