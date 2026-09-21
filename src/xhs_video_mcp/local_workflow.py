"""Render authorized local images without accessing Xiaohongshu."""

import argparse
import json
from pathlib import Path

from .config import config
from .video_maker import create_video


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--duration", type=float, default=3)
    parser.add_argument("--bgm", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--style-prompt", default="")
    args = parser.parse_args()
    if args.output_dir:
        config.output_dir = args.output_dir.expanduser().resolve()
    try:
        result = create_video(
            args.images, bgm_path=args.bgm, duration_per_image=args.duration,
            style_prompt=args.style_prompt,
        )
        payload = {"ok": True, "video_path": str(result.video_path),
                   "duration": result.duration, "images_count": result.images_count,
                   "style_report": result.style_report}
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
    print(json.dumps(payload, ensure_ascii=False))
    if not payload["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
