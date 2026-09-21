"""CLI workflow for OpenClaw/Telegram automation."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys

from .safety import public_url
from .config import config
from .video_maker import create_video_async
from .xhs_parser import parse_xhs_url


async def run_workflow(
    url: str,
    bgm: str = "random",
    duration_per_image: int = 3,
    style_prompt: str = "",
) -> dict:
    """Run the full workflow and return JSON-safe result dict."""
    post = await parse_xhs_url(url)
    temp_dir = post.temp_dir

    try:
        if not post.image_paths:
            return {"ok": False, "error": "No images found in the post", "url": public_url(url)}

        bgm_path = None
        if bgm.lower() == "random":
            bgm_path = config.get_random_bgm()
        elif bgm.lower() != "none":
            bgm_path = config.get_bgm_by_name(bgm)

        result = await create_video_async(
            image_paths=post.image_paths,
            bgm_path=bgm_path,
            duration_per_image=duration_per_image,
            output_filename=f"xhs_{post.post_id}_{post.temp_dir.name}",
            style_prompt=style_prompt,
        )

        return {
            "ok": True,
            "video_path": str(result.video_path),
            "images_count": result.images_count,
            "duration": result.duration,
            "bgm_used": result.bgm_used,
            "title": post.title,
            "style_report": result.style_report,
        }
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    """Build workflow CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Generate a short video from Xiaohongshu URL."
    )
    parser.add_argument("--url", required=True, help="Xiaohongshu URL")
    parser.add_argument(
        "--bgm",
        default="random",
        help='BGM file name, "random", or "none"',
    )
    parser.add_argument(
        "--duration-per-image",
        type=int,
        default=3,
        help="Seconds each image is shown",
    )
    parser.add_argument(
        "--style-prompt",
        default="",
        help='Natural-language style prompt, e.g. 在第一张图把"马云"红笔圈出来',
    )
    return parser


def main() -> None:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args()

    try:
        result = asyncio.run(
            run_workflow(
                url=args.url,
                bgm=args.bgm,
                duration_per_image=args.duration_per_image,
                style_prompt=args.style_prompt,
            )
        )
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "url": public_url(args.url)}

    print(json.dumps(result, ensure_ascii=False))
    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
