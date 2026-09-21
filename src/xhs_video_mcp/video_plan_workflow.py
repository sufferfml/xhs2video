"""Planning workflow for Step-2 Telegram requests.

This module does not parse free-form requirements with regex heuristics anymore.
It expects a model-produced structured JSON payload, validates it with deterministic
rules, then writes a render plan used by Step-3.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import shlex
from typing import Any

from .config import config
from .safety import require_job_dir, within_job
from .video_maker import _build_ffmpeg_command, _resolve_transition_for_durations


@dataclass
class StructuredPlan:
    """Validated structured values used for render planning."""

    selected_indices: list[int]
    duration_per_image: int
    duration_per_image_list: list[int] | None
    bgm: str
    style_prompt: str
    ffmpeg_goal: str
    warnings: list[str]


def _resolve_download_dir(download_dir: Path | None) -> Path:
    return require_job_dir(download_dir)


def _load_images(download_dir: Path) -> list[Path]:
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


def _load_model_plan_json_text(
    model_plan_json: str | None,
    model_plan_path: Path | None,
) -> str:
    if model_plan_json and model_plan_json.strip():
        return model_plan_json.strip()
    if model_plan_path:
        if not model_plan_path.exists():
            raise FileNotFoundError(f"model_plan_path not found: {model_plan_path}")
        return model_plan_path.read_text(encoding="utf-8").strip()
    raise ValueError(
        "model_plan_json is required. Free-form request parsing is disabled. "
        "Provide --model-plan-json or --model-plan-path."
    )


def _parse_model_plan_payload(model_plan_json_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(model_plan_json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid model_plan_json: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("model_plan_json must be a JSON object.")
    return payload


def _coerce_index(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must contain integers.")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError(f"{field_name} contains a non-integer value: {value!r}")


def _coerce_duration(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric seconds.")
    if isinstance(value, int):
        duration = value
    elif isinstance(value, float) and value.is_integer():
        duration = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        duration = int(value.strip())
    else:
        raise ValueError(f"{field_name} must be numeric seconds, got: {value!r}")

    if not 1 <= duration <= 12:
        raise ValueError(f"{field_name} must be between 1 and 12 seconds.")
    return duration


def _extract_selected_indices(
    payload: dict[str, Any],
    max_images: int,
) -> list[int]:
    raw_indices = payload.get("selected_indices")
    if raw_indices is None:
        nested = payload.get("render_plan")
        if isinstance(nested, dict):
            raw_indices = nested.get("selected_indices")
    if not isinstance(raw_indices, list) or not raw_indices:
        raise ValueError("model_plan_json must include non-empty selected_indices list.")

    selected: list[int] = []
    for item in raw_indices:
        index = _coerce_index(item, "selected_indices")
        if index < 1 or index > max_images:
            raise ValueError(
                f"selected_indices contains out-of-range index {index}. "
                f"Allowed range: 1..{max_images}."
            )
        if index not in selected:
            selected.append(index)

    if not selected:
        raise ValueError("selected_indices resolved to empty after validation.")
    return selected


def _resolve_render_plan_source(payload: dict[str, Any]) -> dict[str, Any]:
    render_plan = payload.get("render_plan")
    if render_plan is None:
        return payload
    if not isinstance(render_plan, dict):
        raise ValueError("render_plan must be a JSON object when provided.")
    return render_plan


def _resolve_bgm_choice(
    raw_choice: Any,
    available_bgm: list[str],
    warnings: list[str],
) -> str:
    if raw_choice is None or not str(raw_choice).strip():
        fallback = "random" if available_bgm else "none"
        warnings.append(
            f"model_plan missing bgm; fallback to '{fallback}'."
        )
        return fallback

    choice = str(raw_choice).strip()
    lowered = choice.lower()
    if lowered in {"none", "random"}:
        if lowered == "random" and not available_bgm:
            warnings.append("bgm=random requested but no BGM files found; fallback to none.")
            return "none"
        return lowered

    if choice in available_bgm:
        return choice

    # Allow stem-only value from model output.
    for file_name in available_bgm:
        if Path(file_name).stem.lower() == lowered:
            return file_name

    fallback = "random" if available_bgm else "none"
    warnings.append(
        f"bgm '{choice}' not found in BGM directory; fallback to '{fallback}'."
    )
    return fallback


def _extract_structured_plan(
    payload: dict[str, Any],
    images_count: int,
    available_bgm: list[str],
) -> StructuredPlan:
    warnings: list[str] = []
    selected_indices = _extract_selected_indices(payload, images_count)
    render_source = _resolve_render_plan_source(payload)

    duration_list_value: list[int] | None = None
    raw_duration_list = render_source.get("duration_per_image_list")
    if raw_duration_list is not None:
        if not isinstance(raw_duration_list, list):
            raise ValueError("duration_per_image_list must be a list.")
        if len(raw_duration_list) != len(selected_indices):
            raise ValueError(
                "duration_per_image_list length must equal selected_indices length."
            )
        duration_list_value = [
            _coerce_duration(item, f"duration_per_image_list[{idx}]")
            for idx, item in enumerate(raw_duration_list)
        ]

    raw_duration = render_source.get("duration_per_image")
    if raw_duration is not None:
        duration_value = _coerce_duration(raw_duration, "duration_per_image")
    elif duration_list_value:
        duration_value = duration_list_value[0]
    else:
        duration_value = int(config.video.duration_per_image)
        warnings.append(
            f"model_plan missing duration_per_image; fallback to default {duration_value}s."
        )

    bgm_value = _resolve_bgm_choice(
        raw_choice=render_source.get("bgm"),
        available_bgm=available_bgm,
        warnings=warnings,
    )

    style_value = render_source.get("style_prompt")
    if style_value is None:
        style_value = ""
        warnings.append("model_plan missing style_prompt; fallback to empty style prompt.")
    if not isinstance(style_value, str):
        raise ValueError("style_prompt must be a string when provided.")
    style_value = style_value.strip()

    goal_value = render_source.get("ffmpeg_goal")
    if goal_value is None:
        goal_value = "基于用户要求生成9:16短视频，带转场和可选BGM。"
    if not isinstance(goal_value, str):
        raise ValueError("ffmpeg_goal must be a string when provided.")
    goal_value = goal_value.strip() or "基于用户要求生成9:16短视频，带转场和可选BGM。"

    return StructuredPlan(
        selected_indices=selected_indices,
        duration_per_image=duration_value,
        duration_per_image_list=duration_list_value,
        bgm=bgm_value,
        style_prompt=style_value,
        ffmpeg_goal=goal_value,
        warnings=warnings,
    )


def _pick_preview_bgm_file(bgm_choice: str, available_bgm: list[str]) -> str | None:
    if bgm_choice == "none":
        return None
    if bgm_choice == "random":
        return sorted(available_bgm)[0] if available_bgm else None
    return bgm_choice if bgm_choice in available_bgm else None


def _build_ffmpeg_preview(
    selected_images: list[Path],
    duration_per_image: int,
    bgm_for_preview: str | None,
    duration_per_image_list: list[int] | None = None,
) -> str:
    vc = config.video
    if (
        duration_per_image_list
        and len(duration_per_image_list) == len(selected_images)
    ):
        image_durations = [float(value) for value in duration_per_image_list]
    else:
        image_durations = [float(duration_per_image) for _ in selected_images]

    transition = _resolve_transition_for_durations(
        vc.transition_duration,
        image_durations,
    )
    total_duration = (
        sum(image_durations) - max(0, len(image_durations) - 1) * transition
    )

    bgm_path = config.get_bgm_by_name(bgm_for_preview) if bgm_for_preview else None
    output_name = datetime.now().strftime("planned_%Y%m%d_%H%M%S")
    output_path = config.output_dir / f"{output_name}.{vc.output_format}"
    ffmpeg_bin = os.environ.get("XHS_FFMPEG_BIN", "ffmpeg")

    command = _build_ffmpeg_command(
        image_paths=selected_images,
        bgm_path=bgm_path,
        image_durations=image_durations,
        transition=transition,
        total_duration=total_duration,
        output_path=output_path,
        video_config=vc,
        ffmpeg_bin=ffmpeg_bin,
    )
    return " ".join(shlex.quote(arg) for arg in command)


def run_workflow(
    download_dir: Path | None,
    request_text: str,
    model_plan_json: str | None = None,
    model_plan_path: Path | None = None,
    duration_per_image: int | None = None,
    bgm: str | None = None,
    style_prompt: str | None = None,
    ffmpeg_goal: str | None = None,
) -> dict[str, Any]:
    config.ensure_directories()
    resolved_download_dir = _resolve_download_dir(download_dir)
    images = _load_images(resolved_download_dir)

    bgm_files = sorted(
        [
            item.name
            for item in config.bgm_dir.iterdir()
            if item.is_file() and item.suffix.lower() in config.supported_audio_formats
        ]
    )

    raw_model_plan_text = _load_model_plan_json_text(
        model_plan_json=model_plan_json,
        model_plan_path=model_plan_path,
    )
    model_payload = _parse_model_plan_payload(raw_model_plan_text)
    structured = _extract_structured_plan(
        payload=model_payload,
        images_count=len(images),
        available_bgm=bgm_files,
    )

    warnings = list(structured.warnings)
    if duration_per_image is not None:
        duration_value = _coerce_duration(duration_per_image, "duration_per_image override")
        duration_list_value = [duration_value for _ in structured.selected_indices]
    else:
        duration_value = structured.duration_per_image
        duration_list_value = structured.duration_per_image_list

    if bgm is not None:
        bgm_choice = _resolve_bgm_choice(
            raw_choice=bgm,
            available_bgm=bgm_files,
            warnings=warnings,
        )
    else:
        bgm_choice = structured.bgm

    style_value = (style_prompt if style_prompt is not None else structured.style_prompt).strip()
    goal_value = (ffmpeg_goal if ffmpeg_goal is not None else structured.ffmpeg_goal).strip()

    selected_paths = [images[index - 1] for index in structured.selected_indices]
    bgm_preview_name = _pick_preview_bgm_file(bgm_choice, bgm_files)
    ffmpeg_preview_command = _build_ffmpeg_preview(
        selected_images=selected_paths,
        duration_per_image=duration_value,
        bgm_for_preview=bgm_preview_name,
        duration_per_image_list=duration_list_value,
    )

    render_plan: dict[str, Any] = {
        "duration_per_image": duration_value,
        "bgm": bgm_choice,
        "bgm_preview_resolved": bgm_preview_name,
        "style_prompt": style_value,
        "ffmpeg_goal": goal_value,
    }
    if duration_list_value:
        render_plan["duration_per_image_list"] = duration_list_value

    plan = {
        "ok": True,
        "planning_mode": "model-structured+rule-validated",
        "download_dir": str(resolved_download_dir),
        "selected_indices": structured.selected_indices,
        "selected_image_paths": [str(path) for path in selected_paths],
        "render_plan": render_plan,
        "warnings": warnings,
        "ffmpeg_preview_command": ffmpeg_preview_command,
    }

    plan_path = resolved_download_dir / "video_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    plan["plan_path"] = str(plan_path)
    return plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate model-structured render planning JSON and write video_plan.json."
        )
    )
    parser.add_argument(
        "--download-dir",
        required=True,
        help=(
            "Directory created by xhs-image-workflow. "
            "Pass the exact download_dir returned by Step-1."
        ),
    )
    parser.add_argument(
        "--request",
        default="",
        help="Accepted for compatibility; original message text is not saved in the plan.",
    )
    parser.add_argument(
        "--model-plan-json",
        help="Structured JSON produced by model parsing.",
    )
    parser.add_argument(
        "--model-plan-path",
        help="Path to a JSON file containing model-structured plan payload.",
    )
    parser.add_argument(
        "--duration-per-image",
        type=int,
        help="Override seconds per image (1..12)",
    )
    parser.add_argument(
        "--bgm",
        help="Override BGM choice: none | random | <exact file name>",
    )
    parser.add_argument(
        "--style-prompt",
        help="Override style prompt for downstream renderer",
    )
    parser.add_argument(
        "--ffmpeg-goal",
        help="Override one-line ffmpeg goal description",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        result = run_workflow(
            download_dir=Path(args.download_dir) if args.download_dir else None,
            request_text=args.request or "",
            model_plan_json=args.model_plan_json,
            model_plan_path=Path(args.model_plan_path) if args.model_plan_path else None,
            duration_per_image=args.duration_per_image,
            bgm=args.bgm,
            style_prompt=args.style_prompt,
            ffmpeg_goal=args.ffmpeg_goal,
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
