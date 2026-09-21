# Release procedure

## One-time preparation for the initial public snapshot

This section describes the initial private-to-public transition, not a step to
repeat for subsequent contributions or releases. Routine releases use the existing
public repository and preserve its Git history.

The old development repository contains private historical information and must
remain private. Publish only the prepared source snapshot with a new Git history.
Do not copy .git, reflogs, old branches/tags, local output, background music,
virtual environments, caches, credentials or development audit files into it.
Do not link old private commits in public release notes. Preserve LICENSE and
THIRD_PARTY_NOTICES.md even though the Git history is new.

Only with the repository owner's explicit authorization should the prepared
repository be made public. Do not repeat the visibility change for routine releases.

## Gate for every release

1. Confirm the intended repository and release commit. Review its history and
   metadata for unintended disclosure. GitHub noreply addresses are recommended
   for authors and committers, but intentionally public work or open-source
   emails are allowed. A non-noreply email warning asks for review, does not print
   the address, and does not block release. Do not rewrite established history
   solely to replace an intentionally public email address.
2. Run `uv sync --locked --extra dev`, `uv run --locked pytest -q`, and
   `uv run --locked python scripts/smoke.py`.
3. Run `uv run --locked python scripts/check_release.py --history` and
   `gitleaks git --redact --log-opts="--all" .` on the exact release commit.
   Email-only warnings are advisory; all blocking privacy findings and Gitleaks
   failures must still be resolved. Do not bypass these commands or suppress
   their nonzero exit status. A passing check does not prove that every email
   address was intentionally made public.
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
8. Only after the repository owner explicitly authorizes the release, create its
   tag and release (v0.2.0a1 is the initial prerelease). Use the existing public
   history, and update versioned commands above for later versions. Use generated
   images for demonstrations. Publishing to PyPI is a separate decision.

If any old secret is discovered, revoke it first. A clean snapshot does not delete
old copies or revoke exposed credentials. The previous private repository and any
local backup still require private storage. Do not make that original repository
public as a shortcut.
