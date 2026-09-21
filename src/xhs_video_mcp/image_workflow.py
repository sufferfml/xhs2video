"""CLI workflow for image-only export from Xiaohongshu links."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from .safety import public_url
from .config import config
from .xhs_parser import parse_xhs_url


XHS_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?(?:xiaohongshu\.com|xhslink\.com)/\S+",
    flags=re.IGNORECASE,
)


def extract_xhs_url(text: str) -> str | None:
    """Extract the first XHS URL from arbitrary text."""
    if not text:
        return None
    match = XHS_URL_PATTERN.search(text)
    if not match:
        return None
    return match.group(0).rstrip('",.;!?)]}>')


def _make_output_dir(base_dir: Path, post_id: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(tempfile.mkdtemp(prefix=f"xhs_{post_id}_{timestamp}_", dir=base_dir))


async def run_workflow(url: str, output_root: Path | None = None) -> dict:
    """Parse a Xiaohongshu link and export all downloaded images."""
    post = await parse_xhs_url(url)
    temp_dir = post.temp_dir
    output_base = output_root or (config.output_dir / "images")
    saved_paths: list[Path] = []

    try:
        output_base.mkdir(parents=True, exist_ok=True, mode=0o700)
        output_dir = _make_output_dir(output_base, post.post_id)
        if not post.image_paths:
            return {"ok": False, "error": "No images found in the post", "url": public_url(url)}

        for index, source_path in enumerate(post.image_paths):
            suffix = source_path.suffix or ".jpg"
            target_path = output_dir / f"image_{index:03d}{suffix}"
            shutil.copy2(source_path, target_path)
            saved_paths.append(target_path)

        return {
            "ok": True,
            "url": public_url(url),
            "post_id": post.post_id,
            "title": post.title,
            "images_count": len(saved_paths),
            "image_paths": [str(path) for path in saved_paths],
            "image_items": [
                {"index": idx + 1, "path": str(path)}
                for idx, path in enumerate(saved_paths)
            ],
            "image_urls": [public_url(item) for item in post.image_urls],
            "download_dir": str(output_dir),
        }
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    """Build workflow CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Download images from a Xiaohongshu URL and print JSON result."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="Xiaohongshu URL")
    group.add_argument("--text", help="Raw message text containing a Xiaohongshu URL")
    parser.add_argument(
        "--output-dir",
        default=str(config.output_dir / "images"),
        help="Directory to store exported images",
    )
    return parser


def main() -> None:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args()

    url = args.url
    if not url:
        url = extract_xhs_url(args.text)
        if not url:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "No Xiaohongshu URL found in --text",
                    },
                    ensure_ascii=False,
                )
            )
            sys.exit(1)

    try:
        result = asyncio.run(
            run_workflow(
                url=url,
                output_root=Path(args.output_dir),
            )
        )
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "url": public_url(url)}

    print(json.dumps(result, ensure_ascii=False))
    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
