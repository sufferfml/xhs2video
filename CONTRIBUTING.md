# Contributing

Use Python 3.10–3.12 and `uv sync --locked --extra dev`. Run `uv run --locked pytest`
and `uv run --locked python scripts/smoke.py`; FFmpeg is required for the latter.
Run the secret/privacy checks described in docs/RELEASE.md before submitting.

Commit author and committer email addresses become part of public Git history.
We recommend your GitHub-provided noreply address, but an intentionally public
work or open-source email is welcome. Confirm your choice before submitting.
The history checker emits a non-blocking, value-redacted warning for non-noreply
emails; it cannot determine whether an address is private. Sensitive files,
tokens and other blocking privacy findings still fail checks.
GitHub's email privacy setting does not change your local Git email configuration
or remove addresses from existing commits. Do not rewrite history solely because
a contributor intentionally uses a public email address.

Use generated fixtures, not real post downloads or account data. Add regression tests
for meaningful behavior changes. Do not rely on live platform requests in CI.
Do not log query tokens or personal routing identifiers. Preserve provenance and
third-party license notices when moving or adapting code.

By contributing, you confirm you may contribute the code and agree it is distributed
under the project's GPL-3.0-only license. Explain any third-party source in the PR.
