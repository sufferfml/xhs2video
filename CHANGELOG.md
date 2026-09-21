# Changelog

## Unreleased

- Make non-noreply commit emails advisory without printing their values; keep
  sensitive-content and secret checks blocking.
- Add privacy-checker regression coverage and clarify contributor email choice
  and the difference between initial open-source preparation and routine releases.

## 0.2.0a1 — 2026-09-21

Initial prepared open-source snapshot (experimental, local single-user).

- Add GPLv3 license and explicit upstream provenance.
- Replace personal integration instructions with portable, privacy-aware examples.
- Store application data in user-owned directories without import-time writes.
- Require explicit job directories; isolate temporary downloads per invocation.
- Validate HTTPS hosts and browser redirects, bound image downloads and rendering,
  and keep signed URLs out of error logs and JSON URL fields.
- Use the official MCP SDK implementation and remove the separate FastMCP dependency.
- Add local-image CLI, generated examples, MCP stdio smoke test and release checks.
- Disable ambiguous `latest`; existing split workflows must pass download_dir.
- No multi-user bot, public server, platform-access authorization or live-platform
  availability guarantee is included in this release.
