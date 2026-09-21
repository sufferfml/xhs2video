# Validation scope — 0.2.0a1

The automated release checks use generated images and mock HTTP responses. They
never sign in, send messages, fetch real posts or require model/API credentials.

- Unit/regression tests cover style selection, duration handling, exact host
  validation, query-token redaction, byte limits, redirect rejection, file scope,
  per-job cleanup, process deadlines and configuration import behavior.
- scripts/smoke.py performs a real offline FFmpeg render, probes the codec,
  resolution and duration, and opens a real MCP stdio connection to initialize,
  list tools and call get_video_config.
- Release-checker regression tests cover advisory author/committer emails,
  redacted output, and blocking sensitive content in working files, deleted
  historical files, commit messages and wheel/source archives.
- CI runs the locked environment on Ubuntu/Python 3.10, 3.11 and 3.12, plus full
  history privacy checks and Gitleaks. Non-noreply commit emails produce advisory
  warnings; sensitive-content and secret findings remain blocking. A warning
  cannot determine whether an email was intentionally published.
  The workflow's existence alone is not proof
  that a given commit passed; inspect that commit's Actions results.
- Local release preparation verifies an isolated macOS/Python 3.11 environment
  and a separately installed wheel. Exact outcomes are recorded in the private
  handoff report rather than embedding personal machine paths in this file.

Not verified by these checks: current Xiaohongshu page compatibility, authorized
live post downloads, OCR accuracy on arbitrary images, Telegram delivery, Windows
setup or concurrent multi-user service operation. These are not promised by the
experimental release. Network/platform access permissions are not granted by the
software license.
