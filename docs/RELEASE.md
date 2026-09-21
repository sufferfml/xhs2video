# Release procedure

The old development repository contains private historical information and must
remain private. Publish only the prepared source snapshot with a new Git history.
Do not copy .git, reflogs, old branches/tags, local output, background music,
virtual environments, caches, credentials or development audit files into it.
Do not link old private commits in public release notes. Preserve LICENSE and
THIRD_PARTY_NOTICES.md even though the Git history is new.

## Gate

1. Confirm the intended repository is the clean snapshot, and that its complete
   history and commit metadata contain only public information. Use a GitHub
   noreply address for both author and committer.
2. Run `uv sync --locked --extra dev`, `uv run --locked pytest -q`, and
   `uv run --locked python scripts/smoke.py`.
3. Run `uv run --locked python scripts/check_release.py --history` and
   `gitleaks git --redact --log-opts="--all" .` on the exact snapshot.
4. Run `uv build`; inspect both wheel and source archive with:
   `uv run --locked python scripts/check_release.py --archive dist/xhs_video_mcp-0.2.0a1.tar.gz --archive dist/xhs_video_mcp-0.2.0a1-py3-none-any.whl`.
   Check that LICENSE and THIRD_PARTY_NOTICES.md are present and no private media
   or configuration is bundled. Install the wheel in a new virtual environment
   and run scripts/smoke.py from outside the source checkout.
5. Review the lock-derived dependency inventory and any security alerts. New
   dependencies or redistributed binaries require a new licensing review.
6. Inspect every to-be-public branch/tag, contributor/commit metadata, Actions
   log/artifact, release attachment and issue/PR. Avoid attaching local logs.
7. Confirm the README's support boundaries match docs/VALIDATION.md. Do not
   advertise live scraping, OCR accuracy, Telegram delivery or multi-user support
   as verified without corresponding evidence and authorization.
8. Only after the repository owner explicitly authorizes publication, change the
   clean repository's visibility and create the v0.2.0a1 prerelease. Use generated
   images for demonstrations. Publishing to PyPI is a separate decision.

If any old secret is discovered, revoke it first. A clean snapshot does not delete
old copies or revoke exposed credentials. The previous private repository and any
local backup still require private storage. Do not make that original repository
public as a shortcut.
