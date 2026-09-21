# Third-party notices and provenance

## Project code

Copyright (C) 2026 sufferfml and contributors. Distributed under GPL-3.0-only;
see LICENSE. Modified release preparation version: 0.2.0a1 (2026-09-21).

## XHS-Downloader — JoeanAmier and contributors

Source: https://github.com/JoeanAmier/XHS-Downloader
License: GNU General Public License version 3 (GPLv3), reproduced in LICENSE.
Reviewed pre-existing upstream revision:
`086ff930e11d1b99abd9fb7310d5552eca43314e`, file `source/application/image.py`.

The CDN token extraction and URL construction in `src/xhs_video_mcp/xhs_parser.py`
follow/adapt XHS-Downloader's image extraction approach. This includes parsing a
CDN path token and constructing the `ci.xiaohongshu.com` image-format URL or
`sns-img-bd.xhscdn.com` automatic-format URL. Upstream attribution is preserved;
we do not claim these parts as independently originated code.

The exact historical source revision used when the project was first written was
not recorded. The revision above is an audit reference predating this project's
initial commit, not a claim of exact original provenance. This release conservatively
uses GPLv3 rather than attempting to relicense the adapted portions permissively.

Modifications include Playwright state extraction, URL normalization, multiple URL
fallbacks, deduplication, image filtering, per-task storage, host validation, bounded
streaming downloads, and sanitized diagnostics. The review found no copyright
header in the referenced upstream image module to reproduce separately. The GPL
text is retained verbatim, including its Free Software Foundation notice.

## Python and system dependencies

Resolved versions and integrity hashes are in uv.lock. Dependencies remain separate
packages; their upstream licenses apply. The source/wheel does not vendor them. The server uses the FastMCP implementation
bundled with the official MCP Python SDK; it does not depend on the separate
FastMCP distribution or its cache/authentication stack.
The build must include this notice and LICENSE. A lock-derived inventory is in
`docs/dependency-licenses.json`; check it again when upgrading.

| Direct dependency | License |
|---|---|
| MCP Python SDK | MIT |
| Playwright Python | Apache-2.0 |
| Pillow | MIT-CMU / historical HPND notices; see the installed version's license |
| HTTPX | BSD-3-Clause |

FFmpeg, Tesseract, language data and Chromium are installed separately by the user.
Their licenses are not replaced by this project's GPL license. If distributing these
binaries in a future installer or container, review that exact build and provide all
required source offers/notices. FFmpeg builds using libx264 can carry GPL obligations:
https://ffmpeg.org/legal.html

## Examples and media

`examples/make_demo.py` generates simple original geometric sample images. No
third-party post, logo, image, video or music is shipped. The code license does not
grant rights to downloaded content, music, or platform access.
