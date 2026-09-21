"""Check actual Git metadata, including GitHub-generated bot commits."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('committer_email,expected_code', [
    ('noreply@github.com', 0),
    ('private-author@example.invalid', 1),
])
def test_release_metadata_policy(tmp_path, committer_email, expected_code):
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'check_release.py'
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'test-contributor',
           'GIT_AUTHOR_EMAIL': 'test-contributor@users.noreply.github.com',
           'GIT_COMMITTER_NAME': 'GitHub', 'GIT_COMMITTER_EMAIL': committer_email}
    def git(*args):
        subprocess.run(['git', *args], cwd=tmp_path, env=env, check=True, capture_output=True)
    git('init', '-b', 'main')
    (tmp_path/'README.md').write_text('Generated fixture; no personal data.')
    git('add', 'README.md')
    git('-c', 'commit.gpgsign=false', 'commit', '-m', 'Fixture')
    result = subprocess.run([sys.executable, str(script), '--history'],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == expected_code
    assert committer_email not in result.stdout
