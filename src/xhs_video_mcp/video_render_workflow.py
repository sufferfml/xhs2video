"""Render workflow for Step-3 Telegram requests (plan -> video)."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from .config import config
from .safety import require_job_dir, within_job
from .video_maker import create_video_async


def _resolve_download_dir(download_dir: Path | None) -> Path:
    return require_job_dir(download_dir)


def _list_images(download_dir: Path) -> list[Path]:
    if not download_dir.exists():
        raise FileNotFoundError(f"download_dir not found: {download_dir}")

    images = sorted(
        [
            path
            for path in download_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
            and path.name.startswith("image_")
        ]
    )
    if not images:
        raise ValueError(f"No image files found in {download_dir}")
    return [within_job(image, download_dir) for image in images]


def _resolve_plan_path(download_dir: Path, plan_path: Path | None) -> Path:
    if plan_path:
        resolved = plan_path
    else:
        resolved = download_dir / "video_plan.json"

    if not resolved.exists():
        raise FileNotFoundError(
            f"video plan not found: {resolved}. Run xhs_video_mcp.video_plan_workflow first."
        )
    return within_job(resolved, download_dir)


def _load_plan(plan_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in plan file: {plan_path}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Plan file must contain a JSON object: {plan_path}")
    return payload


def _extract_index_from_path(path: Path) -> int | None:
    match = re.search(r"image_(\d{3})", path.stem)
    if not match:
        return None
    return int(match.group(1)) + 1


def _resolve_selected_images(
    plan: dict[str, Any],
    download_dir: Path,
) -> tuple[list[int], list[Path]]:
    all_images = _list_images(download_dir)
    image_by_index = {idx + 1: path for idx, path in enumerate(all_images)}

    raw_paths = plan.get("selected_image_paths")
    if isinstance(raw_paths, list) and raw_paths:
        selected_paths: list[Path] = []
        selected_indices: list[int] = []
        for item in raw_paths:
            if not isinstance(item, str):
                continue
            path = within_job(Path(item), download_dir)
            if path not in {image.resolve() for image in all_images}:
                raise ValueError("Plan image is not an image from the chosen job.")
            selected_paths.append(path)
            index = _extract_index_from_path(path)
            if index is not None:
                selected_indices.append(index)

        if selected_paths:
            if len(selected_indices) != len(selected_paths):
                selected_indices = []
            return selected_indices, selected_paths

    raw_indices = plan.get("selected_indices")
    if isinstance(raw_indices, list) and raw_indices:
        selected_indices = []
        selected_paths = []
        for item in raw_indices:
            if not isinstance(item, int):
                continue
            image_path = image_by_index.get(item)
            if image_path is None:
                continue
            selected_indices.append(item)
            selected_paths.append(image_path)
        if selected_paths:
            return selected_indices, selected_paths

    raise ValueError("Plan does not contain valid selected images.")


def _resolve_durations(
    plan: dict[str, Any],
    duration_override: int | None,
    images_count: int,
) -> tuple[int, list[float]]:
    if images_count <= 0:
        raise ValueError("No selected images to render.")

    if duration_override is not None:
        if not 1 <= duration_override <= 12:
            raise ValueError("duration_per_image override must be between 1 and 12")
        return duration_override, [float(duration_override) for _ in range(images_count)]

    render_plan = plan.get("render_plan", {})
    if isinstance(render_plan, dict):
        list_value = render_plan.get("duration_per_image_list")
        if isinstance(list_value, list) and len(list_value) == images_count:
            durations: list[float] = []
            valid = True
            for item in list_value:
                if not isinstance(item, (int, float)):
                    valid = False
                    break
                duration = float(item)
                if duration < 1 or duration > 12:
                    valid = False
                    break
                durations.append(duration)
            if valid and durations:
                return int(round(durations[0])), durations

        value = render_plan.get("duration_per_image")
        if isinstance(value, int) and 1 <= value <= 12:
            return value, [float(value) for _ in range(images_count)]
    default_duration = config.video.duration_per_image
    return default_duration, [float(default_duration) for _ in range(images_count)]


def _resolve_style_prompt(plan: dict[str, Any], style_override: str | None) -> str:
    if style_override is not None:
        return style_override.strip()

    render_plan = plan.get("render_plan", {})
    if isinstance(render_plan, dict):
        value = render_plan.get("style_prompt")
        if isinstance(value, str):
            return value.strip()
    return ""


def _resolve_bgm_choice(plan: dict[str, Any], bgm_override: str | None) -> str:
    if bgm_override:
        return bgm_override.strip()

    render_plan = plan.get("render_plan", {})
    if isinstance(render_plan, dict):
        value = render_plan.get("bgm")
        if isinstance(value, str) and value.strip():
            return value.strip()

    return "random"


def _resolve_bgm_path(choice: str) -> tuple[str, Path | None]:
    lowered = choice.lower()
    if lowered == "none":
        return "none", None

    if lowered == "random":
        bgm_path = config.get_random_bgm()
        if bgm_path is None:
            return "none", None
        return "random", bgm_path

    bgm_path = config.get_bgm_by_name(choice)
    if bgm_path is None:
        raise ValueError(f"BGM file not found: {choice}")
    return choice, bgm_path


def _default_output_filename(download_dir: Path) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{download_dir.name}_video_{timestamp}"


async def run_workflow(
    download_dir: Path | None = None,
    plan_path: Path | None = None,
    output_filename: str | None = None,
    duration_per_image: int | None = None,
    bgm: str | None = None,
    style_prompt: str | None = None,
) -> dict[str, Any]:
    config.ensure_directories()
    resolved_download_dir = _resolve_download_dir(download_dir)
    resolved_plan_path = _resolve_plan_path(resolved_download_dir, plan_path)
    plan = _load_plan(resolved_plan_path)

    selected_indices, selected_paths = _resolve_selected_images(plan, resolved_download_dir)
    duration_value, duration_values = _resolve_durations(
        plan=plan,
        duration_override=duration_per_image,
        images_count=len(selected_paths),
    )
    style_value = _resolve_style_prompt(plan, style_prompt)
    bgm_choice = _resolve_bgm_choice(plan, bgm)
    applied_bgm_choice, bgm_path = _resolve_bgm_path(bgm_choice)

    result = await create_video_async(
        image_paths=selected_paths,
        bgm_path=bgm_path,
        duration_per_image=duration_value,
        duration_per_image_list=duration_values,
        output_filename=output_filename or _default_output_filename(resolved_download_dir),
        style_prompt=style_value,
    )

    payload = {
        "ok": True,
        "download_dir": str(resolved_download_dir),
        "plan_path": str(resolved_plan_path),
        "selected_indices": selected_indices,
        "selected_image_paths": [str(path) for path in selected_paths],
        "render_applied": {
            "duration_per_image": duration_value,
            "duration_per_image_list": duration_values,
            "bgm": applied_bgm_choice,
            "style_prompt": style_value,
        },
        "video_path": str(result.video_path),
        "duration": result.duration,
        "images_count": result.images_count,
        "bgm_used": result.bgm_used,
        "style_report": result.style_report,
    }

    result_path = resolved_download_dir / "video_result.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["result_path"] = str(result_path)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render video from Step-2 plan and output JSON result."
    )
    parser.add_argument(
        "--download-dir",
        required=True,
        help="Exact directory returned by xhs-image-workflow.",
    )
    parser.add_argument(
        "--plan-path",
        help="Path to video_plan.json. Defaults to <download-dir>/video_plan.json.",
    )
    parser.add_argument("--output-filename", help="Custom output filename without extension")
    parser.add_argument(
        "--duration-per-image",
        type=int,
        help="Override seconds per image (1..12)",
    )
    parser.add_argument("--bgm", help="Override BGM: none | random | <exact file name>")
    parser.add_argument("--style-prompt", help="Override style prompt used by renderer")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        result = asyncio.run(
            run_workflow(
                download_dir=Path(args.download_dir) if args.download_dir else None,
                plan_path=Path(args.plan_path) if args.plan_path else None,
                output_filename=args.output_filename,
                duration_per_image=args.duration_per_image,
                bgm=args.bgm,
                style_prompt=args.style_prompt,
            )
        )
    except Exception as exc:
        result = {
            "ok": False,
            "error": str(exc),
            "download_dir": args.download_dir,
        }

    print(json.dumps(result, ensure_ascii=False))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
