"""Video generation module using local FFmpeg."""

from __future__ import annotations

import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from .config import VideoConfig, config
from .style_engine import (
    StylePlan,
    StyleReport,
    apply_style_plan,
    parse_style_prompt,
    resolve_circle_animation_targets,
    resolve_underline_animation_targets,
)


@dataclass
class VideoResult:
    """Result of video generation."""

    video_path: Path
    duration: float
    images_count: int
    bgm_used: str | None
    style_report: dict | None = None


@dataclass
class CircleOverlaySpec:
    """One animated circle overlay clip anchored on the timeline."""

    clip_path: Path
    start_time: float
    end_time: float
    cycle_duration: float
    boxes_count: int
    style_kind: str = "circle"


def create_video(
    image_paths: list[Path],
    bgm_path: Path | None = None,
    duration_per_image: int | None = None,
    duration_per_image_list: list[int | float] | None = None,
    output_filename: str | None = None,
    video_config: VideoConfig | None = None,
    style_prompt: str = "",
) -> VideoResult:
    """
    Create a vertical video from images with optional BGM and style annotations.

    Args:
        image_paths: List of local image paths.
        bgm_path: Path to BGM audio file (optional).
        duration_per_image: Seconds per image (overrides config).
        duration_per_image_list: Optional per-image duration list, e.g. [5, 3].
        output_filename: Custom output filename (without extension).
        video_config: Video settings (uses default config when omitted).
        style_prompt: Natural-language style instructions, e.g.
            '在第一张图里把"马云"蓝色高亮并红笔圈出来'.

    Returns:
        VideoResult with output path and metadata.
    """
    if not image_paths:
        raise ValueError("No images provided")
    if len(image_paths) > config.max_images:
        raise ValueError(f"At most {config.max_images} images are supported per render.")
    if output_filename and not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", output_filename):
        raise ValueError("output_filename must contain only letters, digits, underscores and hyphens.")
    for path in image_paths:
        with Image.open(path) as image:
            if image.width * image.height > config.max_image_pixels:
                raise ValueError("Image pixel limit exceeded.")
    config.ensure_directories()

    vc = video_config or config.video
    image_durations = _resolve_image_durations(
        image_count=len(image_paths),
        duration_per_image=duration_per_image,
        duration_per_image_list=duration_per_image_list,
        default_duration=float(vc.duration_per_image),
    )

    ffmpeg_bin = os.environ.get("XHS_FFMPEG_BIN", "ffmpeg")
    if not shutil.which(ffmpeg_bin):
        raise RuntimeError(
            f"ffmpeg binary not found: {ffmpeg_bin}. "
            "Install ffmpeg or set XHS_FFMPEG_BIN."
        )

    # Optionally apply style prompt before rendering.
    style_plan = StylePlan(raw_prompt="", actions=[], warnings=[])
    working_images = image_paths
    circle_targets: dict[int, list[tuple[int, int, int, int]]] = {}
    underline_targets: dict[int, list[tuple[int, int, int, int]]] = {}
    circle_debug_targets: list[dict] = []
    underline_debug_targets: list[dict] = []
    style_warnings: list[str] = []
    highlight_applied = 0
    circle_targets_count = 0
    underline_targets_count = 0
    circle_stroke_color = (238, 39, 35, 232)
    underline_stroke_color = (236, 35, 30, 220)

    if style_prompt.strip():
        style_plan = parse_style_prompt(style_prompt, len(image_paths))
        style_warnings.extend(style_plan.warnings)

        highlight_actions = [
            action for action in style_plan.actions if action.kind == "highlight_keyword"
        ]
        circle_actions = [
            action for action in style_plan.actions if action.kind == "circle_keyword"
        ]
        underline_actions = [
            action for action in style_plan.actions if action.kind == "underline_keyword"
        ]

        if highlight_actions:
            style_dir = image_paths[0].parent / "styled"
            highlight_plan = StylePlan(
                raw_prompt=style_plan.raw_prompt,
                actions=highlight_actions,
                warnings=[],
            )
            working_images, highlight_report = apply_style_plan(
                image_paths=image_paths,
                style_plan=highlight_plan,
                styled_dir=style_dir,
            )
            highlight_applied = highlight_report.actions_applied
            style_warnings.extend(highlight_report.warnings)

        if circle_actions:
            circle_stroke_color = _resolve_action_stroke_color(
                circle_actions,
                default_color=circle_stroke_color,
            )
            circle_targets, circle_warnings, circle_targets_count, circle_debug_targets = (
                resolve_circle_animation_targets(
                    image_paths=image_paths,
                    actions=circle_actions,
                )
            )
            style_warnings.extend(circle_warnings)

        if underline_actions:
            underline_stroke_color = _resolve_action_stroke_color(
                underline_actions,
                default_color=underline_stroke_color,
            )
            (
                underline_targets,
                underline_warnings,
                underline_targets_count,
                underline_debug_targets,
            ) = resolve_underline_animation_targets(
                image_paths=image_paths,
                actions=underline_actions,
            )
            style_warnings.extend(underline_warnings)

    transition = _resolve_transition_for_durations(
        transition_duration=vc.transition_duration,
        image_durations=image_durations,
    )
    image_windows = _build_image_windows(image_durations=image_durations, transition=transition)
    total_duration = _compute_total_duration(
        image_durations=image_durations,
        transition=transition,
    )

    if not output_filename:
        output_filename = f"xhs_video_{uuid.uuid4().hex[:8]}"
    output_path = config.output_dir / f"{output_filename}.{vc.output_format}"

    needs_circle_animation = bool(circle_targets)
    needs_underline_animation = bool(underline_targets)
    base_output_path = (
        output_path.with_name(f"{output_path.stem}_base.{vc.output_format}")
        if needs_circle_animation or needs_underline_animation
        else output_path
    )

    command = _build_ffmpeg_command(
        image_paths=working_images,
        bgm_path=bgm_path if bgm_path and bgm_path.exists() else None,
        image_durations=image_durations,
        transition=transition,
        total_duration=total_duration,
        output_path=base_output_path,
        video_config=vc,
        ffmpeg_bin=ffmpeg_bin,
    )
    _run_ffmpeg(command)

    circle_applied = 0
    underline_applied = 0
    final_video_path = base_output_path
    if needs_circle_animation or needs_underline_animation:
        try:
            with tempfile.TemporaryDirectory(prefix="xhs-circle-", dir=config.temp_dir) as temp_dir:
                overlay_specs: list[CircleOverlaySpec] = []
                overlay_warnings: list[str] = []

                circle_rendered = 0
                if needs_circle_animation:
                    (
                        circle_overlays,
                        circle_rendered,
                        circle_overlay_warnings,
                    ) = _build_circle_overlay_specs(
                        image_paths=image_paths,
                        circle_targets=circle_targets,
                        image_windows=image_windows,
                        image_durations=image_durations,
                        video_config=vc,
                        ffmpeg_bin=ffmpeg_bin,
                        overlay_root=Path(temp_dir),
                        stroke_color=circle_stroke_color,
                    )
                    overlay_specs.extend(circle_overlays)
                    overlay_warnings.extend(circle_overlay_warnings)

                underline_rendered = 0
                if needs_underline_animation:
                    (
                        underline_overlays,
                        underline_rendered,
                        underline_overlay_warnings,
                    ) = _build_underline_overlay_specs(
                        image_paths=image_paths,
                        underline_targets=underline_targets,
                        image_windows=image_windows,
                        image_durations=image_durations,
                        video_config=vc,
                        ffmpeg_bin=ffmpeg_bin,
                        overlay_root=Path(temp_dir),
                        stroke_color=underline_stroke_color,
                    )
                    overlay_specs.extend(underline_overlays)
                    overlay_warnings.extend(underline_overlay_warnings)
                style_warnings.extend(overlay_warnings)

                if overlay_specs:
                    _compose_circle_overlays(
                        base_video_path=base_output_path,
                        output_path=output_path,
                        overlay_specs=overlay_specs,
                        video_config=vc,
                        ffmpeg_bin=ffmpeg_bin,
                    )
                    final_video_path = output_path
                    circle_applied = circle_rendered
                    underline_applied = underline_rendered
                    if base_output_path.exists():
                        base_output_path.unlink()
                else:
                    style_warnings.append(
                        "Animation requested but no valid overlay clips were generated."
                    )
        except Exception as exc:
            style_warnings.append(
                f"Circle animation failed, fallback to non-animated result: {exc}"
            )

        if final_video_path != output_path and base_output_path.exists():
            if output_path.exists():
                output_path.unlink()
            shutil.move(str(base_output_path), str(output_path))
            final_video_path = output_path

    style_report: StyleReport | None = None
    if style_prompt.strip():
        style_report = StyleReport(
            prompt=style_plan.raw_prompt,
            actions_planned=len(style_plan.actions),
            actions_applied=highlight_applied + circle_applied + underline_applied,
            warnings=_dedupe_warnings(style_warnings),
            debug_targets=(
                circle_debug_targets + underline_debug_targets
                if circle_debug_targets or underline_debug_targets
                else None
            ),
        )
        if circle_targets_count and circle_applied < circle_targets_count:
            style_report.warnings.append(
                f"Animated circle coverage: {circle_applied}/{circle_targets_count} target boxes."
            )
        if underline_targets_count and underline_applied < underline_targets_count:
            style_report.warnings.append(
                f"Animated underline coverage: {underline_applied}/{underline_targets_count} target boxes."
            )

    return VideoResult(
        video_path=final_video_path,
        duration=float(total_duration),
        images_count=len(working_images),
        bgm_used=bgm_path.name if bgm_path and bgm_path.exists() else None,
        style_report=style_report.to_dict() if style_report else None,
    )


def _dedupe_warnings(warnings: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for warning in warnings:
        normalized = warning.strip()
        if not normalized or normalized in seen:
            continue
        ordered.append(normalized)
        seen.add(normalized)
    return ordered


def _resolve_action_stroke_color(
    actions: list,
    default_color: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    for action in actions:
        color_name = getattr(action, "color", None)
        if not color_name:
            continue
        return _color_name_to_rgba(str(color_name), default_color)
    return default_color


def _color_name_to_rgba(
    color_name: str,
    fallback: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    normalized = (color_name or "").strip().lower()
    mapping: dict[str, tuple[int, int, int, int]] = {
        "red": (238, 39, 35, 232),
        "blue": (38, 120, 255, 232),
        "yellow": (246, 197, 42, 232),
        "green": (52, 186, 98, 232),
        "white": (250, 250, 250, 232),
        "black": (30, 30, 30, 232),
    }
    return mapping.get(normalized, fallback)


def _build_circle_overlay_specs(
    image_paths: list[Path],
    circle_targets: dict[int, list[tuple[int, int, int, int]]],
    image_windows: list[tuple[float, float]],
    image_durations: list[float],
    video_config: VideoConfig,
    ffmpeg_bin: str,
    overlay_root: Path,
    stroke_color: tuple[int, int, int, int],
) -> tuple[list[CircleOverlaySpec], int, list[str]]:
    if not circle_targets:
        return [], 0, []

    overlay_root.mkdir(parents=True, exist_ok=True)
    width, height = video_config.resolution

    overlay_specs: list[CircleOverlaySpec] = []
    warnings: list[str] = []
    rendered_boxes = 0

    for image_index in sorted(circle_targets):
        if image_index < 0 or image_index >= len(image_paths):
            warnings.append(f"Circle target image index out of range: {image_index + 1}")
            continue
        if image_index >= len(image_windows) or image_index >= len(image_durations):
            warnings.append(f"Circle target has no timing window: image {image_index + 1}")
            continue

        source_image = image_paths[image_index]
        source_boxes = circle_targets.get(image_index, [])
        if not source_boxes:
            continue

        try:
            with Image.open(source_image) as image:
                src_width, src_height = image.size
        except Exception as exc:
            warnings.append(f"{source_image.name}: unable to open image for animation: {exc}")
            continue

        mapped_boxes: list[tuple[int, int, int, int]] = []
        for source_box in source_boxes:
            mapped = _project_box_to_canvas(
                box=source_box,
                src_width=src_width,
                src_height=src_height,
                dst_width=width,
                dst_height=height,
            )
            if mapped is None:
                continue
            expanded = _expand_box_for_circle(
                box=mapped,
                frame_width=width,
                frame_height=height,
            )
            if expanded is None:
                continue
            mapped_boxes.append(expanded)

        mapped_boxes = _dedupe_boxes(mapped_boxes)
        if not mapped_boxes:
            warnings.append(
                f"{source_image.name}: circle target outside viewport after 9:16 fit/pad."
            )
            continue

        cycle_duration = _resolve_circle_animation_duration(
            image_duration=image_durations[image_index],
            boxes_count=len(mapped_boxes),
        )
        image_start, image_end = image_windows[image_index]
        image_window = max(0.0, image_end - image_start)
        start_offset = min(0.20, max(0.04, image_window * 0.06))
        start_time = image_start + start_offset
        available = max(0.0, image_window - start_offset)
        if available <= 0.0:
            warnings.append(f"{source_image.name}: no timeline space for circle animation.")
            continue

        full_cycles = int(available // cycle_duration) if cycle_duration > 0 else 0
        if full_cycles <= 0:
            full_cycles = 1
        end_time = min(image_end, start_time + full_cycles * cycle_duration)
        if end_time - start_time < 0.2:
            warnings.append(
                f"{source_image.name}: animation window too short, skipped."
            )
            continue

        clip_path = overlay_root / f"circle_overlay_{image_index:03d}.mov"
        _render_circle_overlay_clip(
            clip_path=clip_path,
            boxes=mapped_boxes,
            width=width,
            height=height,
            duration=cycle_duration,
            fps=video_config.fps,
            ffmpeg_bin=ffmpeg_bin,
            seed_tag=f"{source_image.name}:{image_index}",
            stroke_color=stroke_color,
        )

        overlay_specs.append(
            CircleOverlaySpec(
                clip_path=clip_path,
                start_time=start_time,
                end_time=end_time,
                cycle_duration=cycle_duration,
                boxes_count=len(mapped_boxes),
                style_kind="circle",
            )
        )
        rendered_boxes += len(mapped_boxes)

    return overlay_specs, rendered_boxes, warnings


def _build_underline_overlay_specs(
    image_paths: list[Path],
    underline_targets: dict[int, list[tuple[int, int, int, int]]],
    image_windows: list[tuple[float, float]],
    image_durations: list[float],
    video_config: VideoConfig,
    ffmpeg_bin: str,
    overlay_root: Path,
    stroke_color: tuple[int, int, int, int],
) -> tuple[list[CircleOverlaySpec], int, list[str]]:
    if not underline_targets:
        return [], 0, []

    overlay_root.mkdir(parents=True, exist_ok=True)
    width, height = video_config.resolution

    overlay_specs: list[CircleOverlaySpec] = []
    warnings: list[str] = []
    rendered_boxes = 0

    for image_index in sorted(underline_targets):
        if image_index < 0 or image_index >= len(image_paths):
            warnings.append(f"Underline target image index out of range: {image_index + 1}")
            continue
        if image_index >= len(image_windows) or image_index >= len(image_durations):
            warnings.append(f"Underline target has no timing window: image {image_index + 1}")
            continue

        source_image = image_paths[image_index]
        source_boxes = underline_targets.get(image_index, [])
        if not source_boxes:
            continue

        try:
            with Image.open(source_image) as image:
                src_width, src_height = image.size
        except Exception as exc:
            warnings.append(f"{source_image.name}: unable to open image for underline animation: {exc}")
            continue

        mapped_boxes: list[tuple[int, int, int, int]] = []
        for source_box in source_boxes:
            mapped = _project_box_to_canvas(
                box=source_box,
                src_width=src_width,
                src_height=src_height,
                dst_width=width,
                dst_height=height,
            )
            if mapped is None:
                continue
            expanded = _expand_box_for_underline(
                box=mapped,
                frame_width=width,
                frame_height=height,
            )
            if expanded is None:
                continue
            mapped_boxes.append(expanded)

        mapped_boxes = _dedupe_boxes(mapped_boxes)
        if not mapped_boxes:
            warnings.append(
                f"{source_image.name}: underline target outside viewport after 9:16 fit/pad."
            )
            continue

        image_start, image_end = image_windows[image_index]
        image_window = max(0.0, image_end - image_start)
        start_offset = min(0.16, max(0.04, image_window * 0.04))
        start_time = image_start + start_offset
        available = max(0.0, image_window - start_offset)
        if available <= 0.0:
            warnings.append(f"{source_image.name}: no timeline space for underline animation.")
            continue

        cycle_duration = _resolve_underline_animation_duration(
            image_duration=image_durations[image_index],
            boxes_count=len(mapped_boxes),
        )
        desired_repeat = 2
        if available >= cycle_duration * desired_repeat:
            repeat_count = desired_repeat
        elif available >= cycle_duration:
            repeat_count = 1
            warnings.append(
                f"{source_image.name}: timeline too short for 2 underline loops, fallback to 1."
            )
        else:
            cycle_duration = max(0.55, available)
            repeat_count = 1
            warnings.append(
                f"{source_image.name}: timeline too short for standard underline duration, auto-compressed."
            )

        end_time = min(image_end, start_time + repeat_count * cycle_duration)
        if end_time - start_time < 0.2:
            warnings.append(
                f"{source_image.name}: underline animation window too short, skipped."
            )
            continue

        clip_path = overlay_root / f"underline_overlay_{image_index:03d}.mov"
        _render_underline_overlay_clip(
            clip_path=clip_path,
            boxes=mapped_boxes,
            width=width,
            height=height,
            duration=cycle_duration,
            fps=video_config.fps,
            ffmpeg_bin=ffmpeg_bin,
            seed_tag=f"{source_image.name}:{image_index}",
            stroke_color=stroke_color,
        )

        overlay_specs.append(
            CircleOverlaySpec(
                clip_path=clip_path,
                start_time=start_time,
                end_time=end_time,
                cycle_duration=cycle_duration,
                boxes_count=len(mapped_boxes),
                style_kind="underline",
            )
        )
        rendered_boxes += len(mapped_boxes)

    return overlay_specs, rendered_boxes, warnings


def _resolve_circle_animation_duration(image_duration: float, boxes_count: int) -> float:
    base = min(2.0, max(0.9, image_duration * 0.38))
    extra = max(0, boxes_count - 1) * 0.24
    return min(max(0.5, image_duration - 0.1), base + extra)


def _resolve_underline_animation_duration(image_duration: float, boxes_count: int) -> float:
    base = min(2.0, max(1.0, image_duration * 0.40))
    extra = max(0, boxes_count - 1) * 0.2
    return min(max(0.6, image_duration - 0.1), base + extra)


def _project_box_to_canvas(
    box: tuple[int, int, int, int],
    src_width: int,
    src_height: int,
    dst_width: int,
    dst_height: int,
) -> tuple[int, int, int, int] | None:
    if src_width <= 0 or src_height <= 0:
        return None

    # Keep full source image visible in 9:16 output (letterbox/pillarbox padding),
    # instead of center-cropping.
    scale = min(dst_width / src_width, dst_height / src_height)
    scaled_width = src_width * scale
    scaled_height = src_height * scale
    pad_x = (dst_width - scaled_width) / 2
    pad_y = (dst_height - scaled_height) / 2

    left, top, right, bottom = box
    mapped_left = left * scale + pad_x
    mapped_top = top * scale + pad_y
    mapped_right = right * scale + pad_x
    mapped_bottom = bottom * scale + pad_y

    clipped_left = max(0.0, mapped_left)
    clipped_top = max(0.0, mapped_top)
    clipped_right = min(float(dst_width - 1), mapped_right)
    clipped_bottom = min(float(dst_height - 1), mapped_bottom)

    if clipped_right - clipped_left < 1 or clipped_bottom - clipped_top < 1:
        return None

    return (
        int(round(clipped_left)),
        int(round(clipped_top)),
        int(round(clipped_right)),
        int(round(clipped_bottom)),
    )


def _expand_box_for_circle(
    box: tuple[int, int, int, int],
    frame_width: int,
    frame_height: int,
) -> tuple[int, int, int, int] | None:
    left, top, right, bottom = box
    current_w = max(1, right - left)
    current_h = max(1, bottom - top)

    min_w = max(120, int(frame_width * 0.22))
    min_h = max(56, int(frame_height * 0.06))
    min_area = max(18000, int(frame_width * frame_height * 0.012))

    target_w = max(current_w, min_w)
    target_h = max(current_h, min_h)
    if target_w * target_h < min_area:
        ratio = target_w / target_h if target_h > 0 else 1.8
        target_h = int(math.sqrt(min_area / max(ratio, 0.25)))
        target_w = int(target_h * ratio)

    target_w = min(target_w, frame_width)
    target_h = min(target_h, frame_height)

    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    new_left = int(round(center_x - target_w / 2.0))
    new_top = int(round(center_y - target_h / 2.0))
    new_right = new_left + target_w
    new_bottom = new_top + target_h

    if new_left < 0:
        new_right -= new_left
        new_left = 0
    if new_top < 0:
        new_bottom -= new_top
        new_top = 0
    if new_right > frame_width:
        shift = new_right - frame_width
        new_left -= shift
        new_right = frame_width
    if new_bottom > frame_height:
        shift = new_bottom - frame_height
        new_top -= shift
        new_bottom = frame_height

    new_left = max(0, new_left)
    new_top = max(0, new_top)
    new_right = min(frame_width, new_right)
    new_bottom = min(frame_height, new_bottom)

    if new_right - new_left < 8 or new_bottom - new_top < 8:
        return None
    return (new_left, new_top, new_right, new_bottom)


def _expand_box_for_underline(
    box: tuple[int, int, int, int],
    frame_width: int,
    frame_height: int,
) -> tuple[int, int, int, int] | None:
    """Keep underline geometry close to the text baseline."""
    left, top, right, bottom = box
    current_w = max(1, right - left)
    current_h = max(1, bottom - top)

    pad_x = max(8, int(current_w * 0.12))
    pad_top = max(1, int(current_h * 0.04))
    pad_bottom = max(4, int(current_h * 0.20))

    new_left = max(0, left - pad_x)
    new_right = min(frame_width, right + pad_x)
    new_top = max(0, top - pad_top)
    new_bottom = min(frame_height, bottom + pad_bottom)

    if new_right - new_left < 8 or new_bottom - new_top < 6:
        return None
    return (new_left, new_top, new_right, new_bottom)


def _render_circle_overlay_clip(
    clip_path: Path,
    boxes: list[tuple[int, int, int, int]],
    width: int,
    height: int,
    duration: float,
    fps: int,
    ffmpeg_bin: str,
    seed_tag: str,
    stroke_color: tuple[int, int, int, int],
) -> None:
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    total_frames = max(2, int(round(duration * fps)))
    per_box_draw = max(0.28, duration * 0.76)
    box_stagger = min(0.28, 0.12 * max(0, len(boxes) - 1))

    stroke_defs: list[list[tuple[float, float]]] = []
    for index, box in enumerate(boxes):
        seed = hash((seed_tag, index, box)) & 0xFFFFFFFF
        primary = _build_handdrawn_ellipse_points(box, seed=seed, jitter=0.04)
        stroke_defs.append(primary)

    with tempfile.TemporaryDirectory(prefix=f"{clip_path.stem}-", dir=clip_path.parent) as frames_dir:
        frames_root = Path(frames_dir)
        for frame_index in range(total_frames):
            current_time = frame_index / float(fps)
            canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(canvas, "RGBA")

            for box_index, primary in enumerate(stroke_defs):
                start_time = box_index * box_stagger
                progress = _clamp01((current_time - start_time) / per_box_draw)
                if progress <= 0:
                    continue
                _draw_animated_stroke(
                    draw=draw,
                    points=primary,
                    progress=progress,
                    width=6,
                    color=stroke_color,
                    head_phase=frame_index * 0.17 + box_index * 0.41,
                    head_jitter=0.32,
                    tail_fade=1.0,
                )

            frame_path = frames_root / f"frame_{frame_index:04d}.png"
            canvas.save(frame_path)

        command = [
            ffmpeg_bin,
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frames_root / "frame_%04d.png"),
            "-c:v",
            "qtrle",
            "-pix_fmt",
            "argb",
            str(clip_path),
        ]
        _run_ffmpeg(command)


def _render_underline_overlay_clip(
    clip_path: Path,
    boxes: list[tuple[int, int, int, int]],
    width: int,
    height: int,
    duration: float,
    fps: int,
    ffmpeg_bin: str,
    seed_tag: str,
    stroke_color: tuple[int, int, int, int],
) -> None:
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    total_frames = max(2, int(round(duration * fps)))
    per_box_draw = max(0.30, duration * 0.78)
    box_stagger = min(0.20, 0.10 * max(0, len(boxes) - 1))

    stroke_defs: list[list[tuple[float, float]]] = []
    for index, box in enumerate(boxes):
        seed = hash((seed_tag, "underline", index, box)) & 0xFFFFFFFF
        primary = _build_handdrawn_underline_points(
            box=box,
            seed=seed,
            frame_width=width,
            frame_height=height,
        )
        stroke_defs.append(primary)

    with tempfile.TemporaryDirectory(prefix=f"{clip_path.stem}-", dir=clip_path.parent) as frames_dir:
        frames_root = Path(frames_dir)
        for frame_index in range(total_frames):
            current_time = frame_index / float(fps)
            canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(canvas, "RGBA")

            for box_index, primary in enumerate(stroke_defs):
                start_time = box_index * box_stagger
                progress = _clamp01((current_time - start_time) / per_box_draw)
                if progress <= 0:
                    continue

                _draw_animated_stroke(
                    draw=draw,
                    points=primary,
                    progress=progress,
                    width=8,
                    color=stroke_color,
                    head_phase=frame_index * 0.11 + box_index * 0.43,
                    head_jitter=0.16,
                    tail_fade=1.0,
                )

            frame_path = frames_root / f"frame_{frame_index:04d}.png"
            canvas.save(frame_path)

        command = [
            ffmpeg_bin,
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frames_root / "frame_%04d.png"),
            "-c:v",
            "qtrle",
            "-pix_fmt",
            "argb",
            str(clip_path),
        ]
        _run_ffmpeg(command)


def _build_handdrawn_underline_points(
    box: tuple[int, int, int, int],
    seed: int,
    frame_width: int,
    frame_height: int,
) -> list[tuple[float, float]]:
    left, top, right, bottom = box
    width = max(1, right - left)
    height = max(1, bottom - top)

    rng = random.Random(seed)
    x_pad = max(7.0, width * 0.08)
    start_x = max(0.0, left - x_pad)
    end_x = min(float(frame_width - 1), right + x_pad)
    span = max(12.0, end_x - start_x)

    base_y = bottom + max(2.0, height * 0.06)
    base_y = min(float(frame_height - 3), base_y)
    main_amp = max(0.7, min(2.1, height * 0.022 + span * 0.0014))
    micro_amp = main_amp * 0.18
    slope = rng.uniform(-1.1, 1.1)
    phase = rng.uniform(0.0, math.tau)
    control_count = 9
    controls = [rng.uniform(-main_amp, main_amp) for _ in range(control_count + 1)]

    points: list[tuple[float, float]] = []
    samples = max(56, min(140, int(span / 5.5)))
    for idx in range(samples + 1):
        t = idx / float(samples)
        x = start_x + span * t
        segment = t * control_count
        anchor_idx = min(control_count - 1, int(segment))
        frac = segment - anchor_idx
        smooth = frac * frac * (3.0 - 2.0 * frac)
        drift = controls[anchor_idx] * (1.0 - smooth) + controls[anchor_idx + 1] * smooth
        wave = math.sin((t * 0.78 + 0.03) * math.tau + phase) * micro_amp
        y = base_y + drift + wave + (t - 0.5) * slope
        y = max(0.0, min(float(frame_height - 1), y))
        points.append((x, y))

    return points


def _build_handdrawn_ellipse_points(
    box: tuple[int, int, int, int],
    seed: int,
    jitter: float,
) -> list[tuple[float, float]]:
    left, top, right, bottom = box
    pad_x = max(10, int((right - left) * 0.18))
    pad_y = max(10, int((bottom - top) * 0.35))
    center_x = (left + right) / 2
    center_y = (top + bottom) / 2
    radius_x = (right - left) / 2 + pad_x
    radius_y = (bottom - top) / 2 + pad_y

    rng = random.Random(seed)
    base_offset = rng.uniform(-0.22, 0.22)
    phase_1 = rng.uniform(0.0, math.tau)
    phase_2 = rng.uniform(0.0, math.tau)
    phase_3 = rng.uniform(0.0, math.tau)

    anchors: list[tuple[float, float]] = []
    anchor_count = 24
    for index in range(anchor_count):
        theta = math.tau * (index / anchor_count) + base_offset
        harmonic = (
            1.0
            + math.sin(theta * 2.0 + phase_1) * (jitter * 0.55)
            + math.sin(theta * 3.0 + phase_2) * (jitter * 0.33)
            + math.sin(theta * 5.0 + phase_3) * (jitter * 0.18)
            + rng.uniform(-jitter * 0.14, jitter * 0.14)
        )
        radial_x = radius_x * harmonic
        radial_y = radius_y * (harmonic + math.sin(theta * 1.7 + phase_2) * jitter * 0.12)
        anchors.append(
            (
                center_x + radial_x * math.cos(theta),
                center_y + radial_y * math.sin(theta),
            )
        )

    return _catmull_rom_closed(anchors, samples_per_segment=10)


def _draw_animated_stroke(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    progress: float,
    width: int,
    color: tuple[int, int, int, int],
    head_phase: float,
    head_jitter: float,
    tail_fade: float,
) -> None:
    if len(points) < 2 or progress <= 0:
        return

    clipped = _slice_polyline(points, _clamp01(progress))
    if len(clipped) < 2:
        return

    alpha = max(0, min(255, int(color[3] * max(0.2, tail_fade))))
    draw.line(
        clipped,
        fill=(color[0], color[1], color[2], alpha),
        width=width,
        joint="curve",
    )

    head_x, head_y = clipped[-1]
    head_x += math.sin(head_phase * 1.8) * head_jitter
    head_y += math.cos(head_phase * 1.6) * head_jitter * 0.9
    head_r = max(2, width // 2)
    draw.ellipse(
        (head_x - head_r, head_y - head_r, head_x + head_r, head_y + head_r),
        fill=color,
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _dedupe_boxes(
    boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    unique: list[tuple[int, int, int, int]] = []
    for box in boxes:
        if box not in unique:
            unique.append(box)
    return unique


def _slice_polyline(points: list[tuple[float, float]], progress: float) -> list[tuple[float, float]]:
    if len(points) < 2:
        return points

    progress = _clamp01(progress)
    if progress <= 0:
        return [points[0]]
    if progress >= 1:
        return points

    lengths = _polyline_segment_lengths(points)
    total = sum(lengths)
    if total <= 1e-6:
        return points

    target = total * progress
    out: list[tuple[float, float]] = [points[0]]
    traversed = 0.0

    for index, seg_len in enumerate(lengths):
        start = points[index]
        end = points[index + 1]
        if traversed + seg_len < target:
            out.append(end)
            traversed += seg_len
            continue

        remain = max(0.0, target - traversed)
        ratio = remain / seg_len if seg_len > 0 else 0.0
        x = start[0] + (end[0] - start[0]) * ratio
        y = start[1] + (end[1] - start[1]) * ratio
        out.append((x, y))
        break

    return out


def _polyline_segment_lengths(points: list[tuple[float, float]]) -> list[float]:
    lengths: list[float] = []
    for index in range(len(points) - 1):
        x1, y1 = points[index]
        x2, y2 = points[index + 1]
        lengths.append(math.hypot(x2 - x1, y2 - y1))
    return lengths


def _catmull_rom_closed(
    points: list[tuple[float, float]],
    samples_per_segment: int,
) -> list[tuple[float, float]]:
    if len(points) < 4:
        return points

    out: list[tuple[float, float]] = []
    count = len(points)
    samples = max(4, samples_per_segment)

    for index in range(count):
        p0 = points[(index - 1) % count]
        p1 = points[index]
        p2 = points[(index + 1) % count]
        p3 = points[(index + 2) % count]

        for step in range(samples):
            t = step / float(samples)
            t2 = t * t
            t3 = t2 * t

            x = 0.5 * (
                (2 * p1[0])
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                (2 * p1[1])
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            out.append((x, y))

    out.append(out[0])
    return out


def _compose_circle_overlays(
    base_video_path: Path,
    output_path: Path,
    overlay_specs: list[CircleOverlaySpec],
    video_config: VideoConfig,
    ffmpeg_bin: str,
) -> None:
    if not overlay_specs:
        raise ValueError("overlay_specs must not be empty")

    sorted_specs = sorted(overlay_specs, key=lambda item: item.start_time)
    command = [ffmpeg_bin, "-y", "-i", str(base_video_path)]
    for spec in sorted_specs:
        command.extend(["-stream_loop", "-1", "-i", str(spec.clip_path)])

    filters: list[str] = []
    current_label = "0:v"
    for input_index, spec in enumerate(sorted_specs, start=1):
        source_label = f"ovsrc{input_index}"
        next_label = f"ov{input_index}"
        filters.append(
            f"[{input_index}:v]"
            f"setpts=PTS-STARTPTS+{spec.start_time:.3f}/TB"
            f"[{source_label}]"
        )
        filters.append(
            f"[{current_label}][{source_label}]"
            f"overlay=0:0:format=auto:eof_action=pass:"
            f"enable='between(t,{spec.start_time:.3f},{spec.end_time:.3f})'"
            f"[{next_label}]"
        )
        current_label = next_label

    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            f"[{current_label}]",
            "-map",
            "0:a?",
            "-r",
            str(video_config.fps),
            "-c:v",
            video_config.codec,
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "medium",
            "-movflags",
            "+faststart",
            "-c:a",
            video_config.audio_codec,
            "-b:a",
            "192k",
            str(output_path),
        ]
    )
    _run_ffmpeg(command)


def _resolve_transition(
    transition_duration: float,
    image_duration: float,
    images_count: int,
) -> float:
    return _resolve_transition_for_durations(
        transition_duration=transition_duration,
        image_durations=[image_duration] * max(0, images_count),
    )


def _resolve_transition_for_durations(
    transition_duration: float,
    image_durations: list[float],
) -> float:
    if len(image_durations) <= 1:
        return 0.0
    if transition_duration <= 0:
        return 0.0

    min_duration = min(image_durations)
    if min_duration <= 0:
        return 0.0
    # xfade requires transition < each clip duration.
    if transition_duration >= min_duration:
        return max(0.0, min_duration - 0.05)
    return transition_duration


def _resolve_image_durations(
    image_count: int,
    duration_per_image: int | float | None,
    duration_per_image_list: list[int | float] | None,
    default_duration: float,
) -> list[float]:
    if image_count <= 0:
        raise ValueError("image_count must be positive")

    if duration_per_image_list is not None:
        if len(duration_per_image_list) != image_count:
            raise ValueError(
                "duration_per_image_list length must match selected image count."
            )
        values = [float(item) for item in duration_per_image_list]
    else:
        value = float(default_duration if duration_per_image is None else duration_per_image)
        values = [value for _ in range(image_count)]

    for value in values:
        if not math.isfinite(value) or not 0 < value <= 12:
            raise ValueError("Each image duration must be finite, greater than 0 and at most 12 seconds")
    if sum(values) > config.max_video_seconds:
        raise ValueError(f"Combined image durations must not exceed {config.max_video_seconds} seconds")
    return values


def _build_image_windows(
    image_durations: list[float],
    transition: float,
) -> list[tuple[float, float]]:
    windows: list[tuple[float, float]] = []
    timeline_cursor = 0.0
    for duration in image_durations:
        start = timeline_cursor
        end = start + duration
        windows.append((start, end))
        timeline_cursor += duration - transition
    return windows


def _compute_total_duration(
    image_durations: list[float],
    transition: float,
) -> float:
    if not image_durations:
        return 0.0
    return sum(image_durations) - max(0, len(image_durations) - 1) * transition


def _build_ffmpeg_command(
    image_paths: list[Path],
    bgm_path: Path | None,
    image_durations: list[float],
    transition: float,
    total_duration: float,
    output_path: Path,
    video_config: VideoConfig,
    ffmpeg_bin: str,
) -> list[str]:
    command = [ffmpeg_bin, "-y"]

    if len(image_paths) != len(image_durations):
        raise ValueError("image_durations length must equal image_paths length")

    for index, image_path in enumerate(image_paths):
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")
        command.extend(
            [
                "-loop",
                "1",
                "-t",
                f"{image_durations[index]:.3f}",
                "-i",
                str(image_path),
            ]
        )

    has_audio = bgm_path is not None and bgm_path.exists()
    if has_audio:
        command.extend(["-stream_loop", "-1", "-i", str(bgm_path)])

    filter_complex, video_label, audio_label = _build_filter_complex(
        image_durations=image_durations,
        total_duration=total_duration,
        transition=transition,
        has_audio=has_audio,
        video_config=video_config,
    )

    command.extend(["-filter_complex", filter_complex, "-map", f"[{video_label}]"])
    if audio_label:
        command.extend(["-map", f"[{audio_label}]"])

    command.extend(
        [
            "-r",
            str(video_config.fps),
            "-c:v",
            video_config.codec,
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "medium",
            "-movflags",
            "+faststart",
        ]
    )

    if audio_label:
        command.extend(["-c:a", video_config.audio_codec, "-b:a", "192k"])
    else:
        command.append("-an")

    command.append(str(output_path))
    return command


def _build_filter_complex(
    image_durations: list[float],
    total_duration: float,
    transition: float,
    has_audio: bool,
    video_config: VideoConfig,
) -> tuple[str, str, str | None]:
    image_count = len(image_durations)
    width, height = video_config.resolution
    filters: list[str] = []

    for idx in range(image_count):
        filters.append(
            f"[{idx}:v]"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            "setsar=1,"
            f"fps={video_config.fps},"
            "format=yuv420p"
            f"[v{idx}]"
        )

    if image_count == 1:
        video_label = "v0"
    elif transition > 0:
        current = "v0"
        image_windows = _build_image_windows(image_durations, transition)
        for idx in range(1, image_count):
            out = f"vx{idx}"
            offset = image_windows[idx][0]
            filters.append(
                f"[{current}][v{idx}]"
                f"xfade=transition=fade:duration={transition:.3f}:offset={offset:.3f}"
                f"[{out}]"
            )
            current = out
        video_label = current
    else:
        concat_inputs = "".join(f"[v{idx}]" for idx in range(image_count))
        filters.append(f"{concat_inputs}concat=n={image_count}:v=1:a=0[vcat]")
        video_label = "vcat"

    audio_label: str | None = None
    if has_audio:
        audio_input_index = image_count
        fade_duration = min(video_config.audio_fade_out_duration, total_duration)
        fade_start = max(total_duration - fade_duration, 0.0)
        filters.append(
            f"[{audio_input_index}:a]"
            f"atrim=0:{total_duration:.3f},"
            "asetpts=PTS-STARTPTS,"
            f"afade=t=out:st={fade_start:.3f}:d={fade_duration:.3f}"
            "[aout]"
        )
        audio_label = "aout"

    return ";".join(filters), video_label, audio_label


def _run_ffmpeg(command: list[str]) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=config.ffmpeg_timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("FFmpeg exceeded the per-process time limit.") from None
    if result.returncode == 0:
        return

    stderr = result.stderr or ""
    stderr_tail = "\n".join(stderr.splitlines()[-40:])
    raise RuntimeError(f"FFmpeg render failed:\n{stderr_tail}")


async def create_video_async(
    image_paths: list[Path],
    bgm_path: Path | None = None,
    duration_per_image: int | None = None,
    duration_per_image_list: list[int | float] | None = None,
    output_filename: str | None = None,
    style_prompt: str = "",
) -> VideoResult:
    """Async wrapper for create_video (runs in thread pool)."""
    import asyncio

    return await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: create_video(
            image_paths=image_paths,
            bgm_path=bgm_path,
            duration_per_image=duration_per_image,
            duration_per_image_list=duration_per_image_list,
            output_filename=output_filename,
            style_prompt=style_prompt,
        ),
    )
