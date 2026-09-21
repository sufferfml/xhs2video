"""Natural-language style planning and image annotation utilities."""

from __future__ import annotations

import csv
import io
import math
import os
import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from .config import config


_CN_NUM_MAP = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


@dataclass
class StyleAction:
    """A single style instruction resolved from natural language."""

    kind: str  # "highlight_keyword" | "circle_keyword" | "underline_keyword"
    keyword: str
    image_index: int | None = None  # None means all images
    color: str | None = None  # e.g. "red" | "blue"


@dataclass
class StylePlan:
    """Structured style instructions parsed from a style prompt."""

    raw_prompt: str
    actions: list[StyleAction]
    warnings: list[str]


@dataclass
class StyleReport:
    """Runtime report for style planning and rendering."""

    prompt: str
    actions_planned: int
    actions_applied: int
    warnings: list[str]
    debug_targets: list[dict] | None = None

    def to_dict(self) -> dict:
        """Convert report to JSON-safe dict."""
        payload = {
            "prompt": self.prompt,
            "actions_planned": self.actions_planned,
            "actions_applied": self.actions_applied,
            "warnings": self.warnings,
        }
        if self.debug_targets:
            payload["debug_targets"] = self.debug_targets
        return payload


@dataclass
class OCRWord:
    """One OCR token with geometry."""

    text: str
    left: int
    top: int
    right: int
    bottom: int
    confidence: float
    line_key: tuple[int, int, int]


def parse_style_prompt(style_prompt: str, images_count: int) -> StylePlan:
    """Parse natural-language prompt into deterministic style actions."""
    prompt = (style_prompt or "").strip()
    if not prompt:
        return StylePlan(raw_prompt="", actions=[], warnings=[])

    normalized = (
        prompt.replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )
    warnings: list[str] = []

    keywords = _extract_keywords(normalized)
    if not keywords:
        return StylePlan(
            raw_prompt=prompt,
            actions=[],
            warnings=["No keyword detected in style_prompt, skip style rendering."],
        )

    wants_circle = _contains_any(
        normalized, ("圈", "红笔", "circle", "圈出", "圈中", "圈起来")
    )
    wants_underline = _contains_any(
        normalized, ("下划线", "划线", "画线", "underline", "底线")
    )
    wants_highlight = _contains_any(
        normalized, ("高亮", "荧光", "highlight", "marker", "底色", "蓝底")
    )

    if not wants_circle and not wants_underline and not wants_highlight:
        # Default behavior: if user only gives a keyword, do a red circle.
        wants_circle = True

    actions: list[StyleAction] = []
    for keyword in keywords:
        context = _extract_keyword_context(normalized, keyword)
        image_index = None
        if _has_style_scoped_image_reference(context):
            image_index = _extract_image_index(context, images_count)
            if image_index is not None and (image_index < 0 or image_index >= images_count):
                warnings.append(
                    "Requested image index "
                    f"{image_index + 1} is out of range, fallback to all images."
                )
                image_index = None

        local_wants_circle = _contains_any(
            context, ("圈", "红笔", "circle", "圈出", "圈中", "圈起来")
        )
        local_wants_underline = _contains_any(
            context, ("下划线", "划线", "画线", "underline", "底线")
        )
        local_wants_highlight = _contains_any(
            context, ("高亮", "荧光", "highlight", "marker", "底色", "蓝底")
        )

        # When a keyword has local action words nearby, prefer local mapping.
        # This avoids applying every action to every keyword in mixed prompts.
        has_local_mapping = (
            local_wants_circle or local_wants_underline or local_wants_highlight
        )
        keyword_wants_circle = local_wants_circle if has_local_mapping else wants_circle
        keyword_wants_underline = (
            local_wants_underline if has_local_mapping else wants_underline
        )
        keyword_wants_highlight = (
            local_wants_highlight if has_local_mapping else wants_highlight
        )

        if not keyword_wants_circle and not keyword_wants_underline and not keyword_wants_highlight:
            keyword_wants_circle = True

        circle_color = _detect_color_for_action(
            context if has_local_mapping else normalized,
            action_tokens=("圈", "circle"),
            fallback="red",
        )
        underline_color = _detect_color_for_action(
            context if has_local_mapping else normalized,
            action_tokens=("下划线", "划线", "画线", "underline", "底线"),
            fallback="red",
        )

        if keyword_wants_highlight:
            actions.append(
                StyleAction(
                    kind="highlight_keyword",
                    keyword=keyword,
                    image_index=image_index,
                )
            )
        if keyword_wants_circle:
            actions.append(
                StyleAction(
                    kind="circle_keyword",
                    keyword=keyword,
                    image_index=image_index,
                    color=circle_color,
                )
            )
        if keyword_wants_underline:
            actions.append(
                StyleAction(
                    kind="underline_keyword",
                    keyword=keyword,
                    image_index=image_index,
                    color=underline_color,
                )
            )

    return StylePlan(raw_prompt=prompt, actions=actions, warnings=warnings)


def apply_style_plan(
    image_paths: list[Path],
    style_plan: StylePlan,
    styled_dir: Path,
) -> tuple[list[Path], StyleReport]:
    """Apply a style plan to images and return styled paths + diagnostics."""
    styled_dir.mkdir(parents=True, exist_ok=True)

    if not style_plan.actions:
        return (
            image_paths,
            StyleReport(
                prompt=style_plan.raw_prompt,
                actions_planned=0,
                actions_applied=0,
                warnings=style_plan.warnings,
            ),
        )

    warnings = list(style_plan.warnings)
    output_paths: list[Path] = []
    applied_total = 0

    for idx, image_path in enumerate(image_paths):
        actions_for_image = [
            action
            for action in style_plan.actions
            if action.image_index is None or action.image_index == idx
        ]
        if not actions_for_image:
            output_paths.append(image_path)
            continue

        styled_path = styled_dir / f"styled_{idx:03d}.png"
        applied, image_warnings = _apply_actions_to_single_image(
            image_path, styled_path, actions_for_image
        )
        warnings.extend(image_warnings)
        applied_total += applied
        output_paths.append(styled_path if applied > 0 else image_path)

    return (
        output_paths,
        StyleReport(
            prompt=style_plan.raw_prompt,
            actions_planned=len(style_plan.actions),
            actions_applied=applied_total,
            warnings=warnings,
        ),
    )


def resolve_circle_animation_targets(
    image_paths: list[Path],
    actions: list[StyleAction],
) -> tuple[dict[int, list[tuple[int, int, int, int]]], list[str], int, list[dict]]:
    """
    Resolve OCR boxes for circle actions to be rendered as timeline animation.

    Returns:
      - map of image index -> deduplicated keyword boxes
      - warning strings
      - number of matched circle boxes
      - debug entries describing per-keyword candidate/selected boxes
    """
    return _resolve_animation_targets(
        image_paths=image_paths,
        actions=actions,
        kind="circle_keyword",
    )


def resolve_underline_animation_targets(
    image_paths: list[Path],
    actions: list[StyleAction],
) -> tuple[dict[int, list[tuple[int, int, int, int]]], list[str], int, list[dict]]:
    """Resolve OCR boxes for underline actions to be rendered as timeline animation."""
    return _resolve_animation_targets(
        image_paths=image_paths,
        actions=actions,
        kind="underline_keyword",
    )


def _resolve_animation_targets(
    image_paths: list[Path],
    actions: list[StyleAction],
    kind: str,
) -> tuple[dict[int, list[tuple[int, int, int, int]]], list[str], int, list[dict]]:
    targets: dict[int, list[tuple[int, int, int, int]]] = {}
    warnings: list[str] = []
    matched_boxes = 0
    debug_targets: list[dict] = []

    if not image_paths or not actions:
        return targets, warnings, matched_boxes, debug_targets

    selected_actions = [action for action in actions if action.kind == kind]
    if not selected_actions:
        return targets, warnings, matched_boxes, debug_targets

    for action in selected_actions:
        if action.image_index is None:
            indices = list(range(len(image_paths)))
        else:
            if action.image_index < 0 or action.image_index >= len(image_paths):
                warnings.append(
                    f"Circle action target out of range: image {action.image_index + 1}"
                )
                continue
            indices = [action.image_index]

        for image_idx in indices:
            image_path = image_paths[image_idx]
            try:
                boxes, ocr_warning = _locate_keyword_boxes(image_path, action.keyword)
            except Exception as exc:
                warnings.append(
                    f"{image_path.name}: OCR failed for '{action.keyword}': {exc}"
                )
                debug_targets.append(
                    {
                        "kind": kind,
                        "keyword": action.keyword,
                        "image_index": image_idx + 1,
                        "candidate_boxes_count": 0,
                        "candidate_boxes": [],
                        "selected_box": None,
                        "select_reason": "ocr_error",
                    }
                )
                continue

            if ocr_warning:
                warnings.append(
                    f"{image_path.name}: {ocr_warning} (keyword: {action.keyword})"
                )
            if not boxes:
                warnings.append(
                    f"{image_path.name}: keyword '{action.keyword}' not found by OCR."
                )
                debug_targets.append(
                    {
                        "kind": kind,
                        "keyword": action.keyword,
                        "image_index": image_idx + 1,
                        "candidate_boxes_count": 0,
                        "candidate_boxes": [],
                        "selected_box": None,
                        "select_reason": "no_match",
                    }
                )
                continue

            selected_boxes = _pick_representative_keyword_boxes(boxes)
            if not selected_boxes:
                warnings.append(
                    f"{image_path.name}: keyword '{action.keyword}' matched empty box set."
                )
                debug_targets.append(
                    {
                        "kind": kind,
                        "keyword": action.keyword,
                        "image_index": image_idx + 1,
                        "candidate_boxes_count": len(boxes),
                        "candidate_boxes": [list(box) for box in boxes],
                        "selected_box": None,
                        "selected_boxes_count": 0,
                        "selected_boxes": [],
                        "select_reason": "empty_after_filter",
                    }
                )
                continue

            existing = targets.setdefault(image_idx, [])
            selected_for_debug: list[list[int]] = []
            for selected_box, select_reason in selected_boxes:
                selected_for_debug.append(list(selected_box))
                if selected_box not in existing:
                    existing.append(selected_box)
                    matched_boxes += 1
                    dedupe_state = "added"
                else:
                    dedupe_state = "duplicate_skipped"

                debug_targets.append(
                    {
                        "kind": kind,
                        "keyword": action.keyword,
                        "image_index": image_idx + 1,
                        "candidate_boxes_count": len(boxes),
                        "candidate_boxes": [list(box) for box in boxes],
                        "selected_box": list(selected_box),
                        "selected_boxes_count": len(selected_boxes),
                        "selected_boxes": selected_for_debug,
                        "select_reason": select_reason,
                        "dedupe_state": dedupe_state,
                        "color": action.color,
                    }
                )

    return targets, warnings, matched_boxes, debug_targets


def _apply_actions_to_single_image(
    source_path: Path,
    styled_path: Path,
    actions: list[StyleAction],
) -> tuple[int, list[str]]:
    image = Image.open(source_path).convert("RGBA")
    draw = ImageDraw.Draw(image, "RGBA")
    warnings: list[str] = []
    applied = 0

    for action in actions:
        try:
            boxes, ocr_warning = _locate_keyword_boxes(source_path, action.keyword)
            if ocr_warning:
                warnings.append(
                    f"{source_path.name}: {ocr_warning} (keyword: {action.keyword})"
                )
            if not boxes:
                warnings.append(
                    f"{source_path.name}: keyword '{action.keyword}' not found by OCR."
                )
                continue
        except Exception as exc:
            warnings.append(
                f"{source_path.name}: OCR failed for '{action.keyword}': {exc}"
            )
            continue

        for box in boxes:
            if action.kind == "highlight_keyword":
                _draw_highlight(draw, box)
                applied += 1
            elif action.kind == "circle_keyword":
                _draw_handdrawn_circle(draw, box, action.keyword)
                applied += 1

    if applied > 0:
        image.convert("RGB").save(styled_path)
    image.close()
    return applied, warnings


def _draw_highlight(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = box
    pad_x = max(8, int((right - left) * 0.16))
    pad_y = max(6, int((bottom - top) * 0.25))
    rect = (left - pad_x, top - pad_y, right + pad_x, bottom + pad_y)
    radius = max(6, int((bottom - top) * 0.4))
    draw.rounded_rectangle(rect, radius=radius, fill=(112, 238, 255, 110))


def _draw_handdrawn_circle(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    keyword: str,
) -> None:
    left, top, right, bottom = box
    pad_x = max(10, int((right - left) * 0.18))
    pad_y = max(10, int((bottom - top) * 0.35))

    center_x = (left + right) / 2
    center_y = (top + bottom) / 2
    radius_x = (right - left) / 2 + pad_x
    radius_y = (bottom - top) / 2 + pad_y

    # Deterministic random so rerenders look stable for the same keyword box.
    seed = hash((keyword, left, top, right, bottom)) & 0xFFFFFFFF
    rng = random.Random(seed)

    for pass_idx in range(2):
        points: list[tuple[float, float]] = []
        for angle_deg in range(0, 370, 10):
            theta = math.radians(angle_deg + rng.uniform(-2.5, 2.5))
            jitter = 1.0 + rng.uniform(-0.08, 0.08)
            x = center_x + radius_x * jitter * math.cos(theta)
            y = center_y + radius_y * jitter * math.sin(theta)
            points.append((x, y))

        draw.line(
            points,
            fill=(241, 32, 34, 235),
            width=6 - pass_idx,
            joint="curve",
        )


def _locate_keyword_boxes(
    image_path: Path, keyword: str
) -> tuple[list[tuple[int, int, int, int]], str | None]:
    words, warning = _extract_ocr_words(image_path, keyword)
    keyword_norm = _normalize_for_match(keyword)
    if not keyword_norm:
        return [], warning

    boxes: list[tuple[int, int, int, int]] = []

    # First pass: direct token match.
    for word in words:
        text_norm = _normalize_for_match(word.text)
        if not text_norm:
            continue
        if not _word_compatible_with_keyword(text_norm, keyword_norm):
            continue
        if _keyword_norm_match(text_norm, keyword_norm):
            boxes.append((word.left, word.top, word.right, word.bottom))

    # Second pass: line-level token sequence match for split OCR tokens.
    line_groups = _build_line_word_groups(words)
    for line_words in line_groups:
        filtered_words = [
            item
            for item in line_words
            if _word_compatible_with_keyword(_normalize_for_match(item.text), keyword_norm)
        ]
        if len(filtered_words) < 2:
            continue
        line_words = filtered_words
        line_words.sort(key=lambda item: item.left)
        for start in range(len(line_words)):
            combined = ""
            left = line_words[start].left
            top = line_words[start].top
            right = line_words[start].right
            bottom = line_words[start].bottom

            for end in range(start, min(start + 10, len(line_words))):
                current = line_words[end]
                combined += _normalize_for_match(current.text)
                right = max(right, current.right)
                bottom = max(bottom, current.bottom)
                top = min(top, current.top)

                if not combined:
                    continue
                if _keyword_norm_match(combined, keyword_norm):
                    boxes.append((left, top, right, bottom))
                    break

    # Fallback: when exact match misses, allow containment match.
    # This keeps defaults deterministic (exact first), while still supporting
    # explicit short keywords like "本地" that may be merged by OCR.
    if not boxes:
        for word in words:
            text_norm = _normalize_for_match(word.text)
            if not text_norm:
                continue
            if not _word_compatible_with_keyword(text_norm, keyword_norm):
                continue
            if keyword_norm in text_norm:
                boxes.append((word.left, word.top, word.right, word.bottom))

    if not boxes:
        for line_words in line_groups:
            filtered_words = [
                item
                for item in line_words
                if _word_compatible_with_keyword(_normalize_for_match(item.text), keyword_norm)
            ]
            if not filtered_words:
                continue
            line_words = filtered_words
            line_words.sort(key=lambda item: item.left)
            for start in range(len(line_words)):
                combined = ""
                left = line_words[start].left
                top = line_words[start].top
                right = line_words[start].right
                bottom = line_words[start].bottom

                for end in range(start, min(start + 10, len(line_words))):
                    current = line_words[end]
                    combined += _normalize_for_match(current.text)
                    right = max(right, current.right)
                    bottom = max(bottom, current.bottom)
                    top = min(top, current.top)

                    if keyword_norm and keyword_norm in combined:
                        boxes.append((left, top, right, bottom))
                        break

    unique = _dedupe_boxes(boxes)
    return unique, warning


def _pick_primary_keyword_box(
    boxes: list[tuple[int, int, int, int]]
) -> tuple[tuple[int, int, int, int] | None, str]:
    if not boxes:
        return None, "no_candidates"
    if len(boxes) == 1:
        return boxes[0], "single_candidate"

    # Prefer the box that best "represents" nearby candidates:
    # - contains the most other boxes (phrase-level box beats token-level boxes)
    # - then fallback to larger area
    scored: list[tuple[int, int, int, int, tuple[int, int, int, int]]] = []
    for outer in boxes:
        contains_count = 0
        for inner in boxes:
            if outer is inner:
                continue
            if _box_contains_or_covers(outer, inner):
                contains_count += 1
        scored.append((contains_count, _box_area(outer), -outer[0], -outer[1], outer))

    scored.sort(reverse=True)
    return scored[0][4], "covers_most_candidates_then_largest_area"


def _pick_representative_keyword_boxes(
    boxes: list[tuple[int, int, int, int]]
) -> list[tuple[tuple[int, int, int, int], str]]:
    if not boxes:
        return []
    if len(boxes) == 1:
        return [(boxes[0], "single_candidate")]

    clusters: list[list[tuple[int, int, int, int]]] = []
    for box in boxes:
        placed = False
        for cluster in clusters:
            if any(_boxes_related(box, existing) for existing in cluster):
                cluster.append(box)
                placed = True
                break
        if not placed:
            clusters.append([box])

    selected: list[tuple[tuple[int, int, int, int], str]] = []
    for cluster in clusters:
        primary, reason = _pick_primary_keyword_box(cluster)
        if primary is None:
            continue
        selected.append((primary, f"cluster_size_{len(cluster)}:{reason}"))

    selected.sort(key=lambda item: (item[0][1], item[0][0], -_box_area(item[0])))
    return selected


def _box_area(box: tuple[int, int, int, int]) -> int:
    left, top, right, bottom = box
    return max(0, right - left) * max(0, bottom - top)


def _box_contains_or_covers(
    outer: tuple[int, int, int, int],
    inner: tuple[int, int, int, int],
) -> bool:
    o_left, o_top, o_right, o_bottom = outer
    i_left, i_top, i_right, i_bottom = inner

    margin = 3
    contains = (
        o_left <= i_left + margin
        and o_top <= i_top + margin
        and o_right >= i_right - margin
        and o_bottom >= i_bottom - margin
    )
    if contains:
        return True

    # If not strict containment, use overlap ratio as fallback.
    inter_left = max(o_left, i_left)
    inter_top = max(o_top, i_top)
    inter_right = min(o_right, i_right)
    inter_bottom = min(o_bottom, i_bottom)
    inter_area = max(0, inter_right - inter_left) * max(0, inter_bottom - inter_top)
    inner_area = _box_area(inner)
    if inner_area <= 0:
        return False
    return inter_area / inner_area >= 0.9


def _boxes_related(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> bool:
    if _box_contains_or_covers(a, b) or _box_contains_or_covers(b, a):
        return True
    if _box_iou(a, b) >= 0.20:
        return True

    a_left, a_top, a_right, a_bottom = a
    b_left, b_top, b_right, b_bottom = b
    a_w = max(1, a_right - a_left)
    a_h = max(1, a_bottom - a_top)
    b_w = max(1, b_right - b_left)
    b_h = max(1, b_bottom - b_top)
    a_cx = (a_left + a_right) / 2.0
    a_cy = (a_top + a_bottom) / 2.0
    b_cx = (b_left + b_right) / 2.0
    b_cy = (b_top + b_bottom) / 2.0

    max_gap_x = max(a_w, b_w) * 0.65
    max_gap_y = max(a_h, b_h) * 0.8
    return abs(a_cx - b_cx) <= max_gap_x and abs(a_cy - b_cy) <= max_gap_y


def _box_iou(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> float:
    a_left, a_top, a_right, a_bottom = a
    b_left, b_top, b_right, b_bottom = b
    inter_left = max(a_left, b_left)
    inter_top = max(a_top, b_top)
    inter_right = min(a_right, b_right)
    inter_bottom = min(a_bottom, b_bottom)
    inter_area = max(0, inter_right - inter_left) * max(0, inter_bottom - inter_top)
    if inter_area <= 0:
        return 0.0
    union = _box_area(a) + _box_area(b) - inter_area
    if union <= 0:
        return 0.0
    return inter_area / union


def _extract_ocr_words(image_path: Path, keyword: str) -> tuple[list[OCRWord], str | None]:
    tesseract_bin = os.environ.get("XHS_TESSERACT_BIN", "tesseract")
    if not shutil.which(tesseract_bin):
        raise RuntimeError(
            "tesseract binary not found. Install tesseract or set XHS_TESSERACT_BIN."
        )

    preferred_lang = os.environ.get("XHS_OCR_LANG", "chi_sim+eng")
    available_langs = _list_tesseract_langs(tesseract_bin)
    lang, warning = _select_tesseract_lang(
        keyword=keyword,
        preferred_lang=preferred_lang,
        available_langs=available_langs,
    )

    psm_env = (os.environ.get("XHS_OCR_PSM") or "4").strip()
    try:
        psm = max(3, min(13, int(psm_env)))
    except ValueError:
        psm = 4

    merged_words: dict[tuple[str, int, int, int, int], OCRWord] = {}
    for candidate_psm in _resolve_ocr_psm_candidates(psm):
        words = _extract_ocr_words_for_psm(
            image_path=image_path,
            tesseract_bin=tesseract_bin,
            lang=lang,
            psm=candidate_psm,
        )
        for word in words:
            key = (_normalize_for_match(word.text), word.left, word.top, word.right, word.bottom)
            existing = merged_words.get(key)
            if existing is None or word.confidence > existing.confidence:
                merged_words[key] = word

    ordered_words = sorted(
        merged_words.values(),
        key=lambda item: (item.top, item.left, -item.confidence),
    )
    return ordered_words, warning


def _resolve_ocr_psm_candidates(primary_psm: int) -> list[int]:
    candidates = [primary_psm, 4, 3, 6]
    resolved: list[int] = []
    for value in candidates:
        clamped = max(3, min(13, int(value)))
        if clamped not in resolved:
            resolved.append(clamped)
    return resolved


def _extract_ocr_words_for_psm(
    image_path: Path,
    tesseract_bin: str,
    lang: str,
    psm: int,
) -> list[OCRWord]:
    command = [
        tesseract_bin,
        str(image_path),
        "stdout",
        "--psm",
        str(psm),
        "-l",
        lang,
        "tsv",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=config.ocr_timeout)
    if result.returncode != 0:
        stderr = result.stderr.strip() or "unknown tesseract error"
        raise RuntimeError(stderr)

    words: list[OCRWord] = []
    reader = csv.DictReader(io.StringIO(result.stdout), delimiter="\t")
    for row in reader:
        if row.get("level") != "5":
            continue
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            confidence = float(row.get("conf", "-1"))
            left = int(row.get("left", "0"))
            top = int(row.get("top", "0"))
            width = int(row.get("width", "0"))
            height = int(row.get("height", "0"))
            block_num = int(row.get("block_num", "0"))
            par_num = int(row.get("par_num", "0"))
            line_num = int(row.get("line_num", "0"))
        except ValueError:
            continue

        if confidence < 20:
            continue

        words.append(
            OCRWord(
                text=text,
                left=left,
                top=top,
                right=left + width,
                bottom=top + height,
                confidence=confidence,
                line_key=(block_num, par_num, line_num),
            )
        )

    return words


def _build_line_word_groups(words: list[OCRWord]) -> list[list[OCRWord]]:
    grouped: dict[tuple[int, int, int], list[OCRWord]] = {}
    for word in words:
        grouped.setdefault(word.line_key, []).append(word)

    groups: list[list[OCRWord]] = [list(items) for items in grouped.values()]
    groups.extend(_build_visual_line_groups(words))
    return groups


def _build_visual_line_groups(words: list[OCRWord]) -> list[list[OCRWord]]:
    if not words:
        return []

    sorted_words = sorted(words, key=lambda item: (((item.top + item.bottom) / 2.0), item.left))
    clusters: list[tuple[list[OCRWord], float, float]] = []
    for word in sorted_words:
        center_y = (word.top + word.bottom) / 2.0
        word_h = max(1.0, float(word.bottom - word.top))
        placed = False
        for index, (cluster_words, cluster_center, cluster_h) in enumerate(clusters):
            tolerance = max(word_h, cluster_h) * 0.68
            if abs(center_y - cluster_center) <= tolerance:
                cluster_words.append(word)
                new_count = float(len(cluster_words))
                next_center = (cluster_center * (new_count - 1.0) + center_y) / new_count
                next_h = (cluster_h * (new_count - 1.0) + word_h) / new_count
                clusters[index] = (cluster_words, next_center, next_h)
                placed = True
                break
        if not placed:
            clusters.append(([word], center_y, word_h))

    visual_groups: list[list[OCRWord]] = []
    for cluster_words, _, _ in clusters:
        if len(cluster_words) < 2:
            continue
        visual_groups.append(sorted(cluster_words, key=lambda item: item.left))
    return visual_groups


def _list_tesseract_langs(tesseract_bin: str) -> set[str]:
    result = subprocess.run(
        [tesseract_bin, "--list-langs"], capture_output=True, text=True, timeout=config.ocr_timeout
    )
    if result.returncode != 0:
        return {"eng"}

    langs: set[str] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("List of available languages"):
            continue
        langs.add(line)
    return langs or {"eng"}


def _select_tesseract_lang(
    keyword: str,
    preferred_lang: str,
    available_langs: set[str],
) -> tuple[str, str | None]:
    requested = [part.strip() for part in preferred_lang.split("+") if part.strip()]
    if not requested:
        requested = ["eng"]

    missing = [item for item in requested if item not in available_langs]
    selected = [item for item in requested if item in available_langs]

    warning: str | None = None
    if _contains_cjk(keyword) and "chi_sim" not in selected:
        warning = (
            "Chinese keyword detected but 'chi_sim' language data is unavailable in "
            "tesseract; OCR accuracy may be low."
        )

    if not selected:
        selected = ["eng"] if "eng" in available_langs else [next(iter(available_langs))]

    if missing:
        missing_text = ", ".join(missing)
        base_warning = f"Tesseract languages missing: {missing_text}. Using {'+'.join(selected)}."
        warning = f"{warning} {base_warning}".strip() if warning else base_warning

    return "+".join(selected), warning


def _extract_keywords(text: str) -> list[str]:
    keywords: list[str] = []

    quote_pattern = r'"([^"]+)"|\'([^\']+)\''
    for match in re.finditer(quote_pattern, text):
        value = match.group(1) or match.group(2) or ""
        value = value.strip()
        if value:
            keywords.append(value)

    if not keywords:
        fallback_patterns = [
            r"关键词(?:是|为|:|：)?\s*([^\s,，。；;！？!?]+)",
            r"圈(?:出|中)?\s*([^\s,，。；;！？!?]+)",
            r"高亮\s*([^\s,，。；;！？!?]+)",
            r"highlight\s+([A-Za-z0-9_\-]+)",
            r"circle\s+([A-Za-z0-9_\-]+)",
        ]
        for pattern in fallback_patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                value = (match.group(1) or "").strip()
                if value:
                    keywords.append(value)

    # Keep deterministic order while removing duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for keyword in keywords:
        if keyword not in seen:
            unique.append(keyword)
            seen.add(keyword)
    return unique[:5]


def _extract_keyword_context(text: str, keyword: str, radius: int = 40) -> str:
    if not text or not keyword:
        return text

    clauses = [part.strip() for part in re.split(r"[，,。；;！!？?\n]+", text) if part.strip()]
    keyword_lower = keyword.lower()
    for clause in clauses:
        if keyword_lower in clause.lower():
            return clause

    lowered = text.lower()
    start_idx = lowered.find(keyword_lower)
    if start_idx < 0:
        return text
    end_idx = start_idx + len(keyword)
    return text[max(0, start_idx - radius): min(len(text), end_idx + radius)]


def _detect_color_for_action(
    text: str,
    action_tokens: tuple[str, ...],
    fallback: str,
) -> str:
    color_tokens = {
        "red": ("红", "红色", "red"),
        "blue": ("蓝", "蓝色", "blue"),
        "yellow": ("黄", "黄色", "yellow"),
        "green": ("绿", "绿色", "green"),
        "white": ("白", "白色", "white"),
        "black": ("黑", "黑色", "black"),
    }
    if not text:
        return fallback

    for color_name, tokens in color_tokens.items():
        for color_token in tokens:
            for action_token in action_tokens:
                pattern_a = re.escape(color_token) + r".{0,8}" + re.escape(action_token)
                pattern_b = re.escape(action_token) + r".{0,8}" + re.escape(color_token)
                if re.search(pattern_a, text, flags=re.IGNORECASE | re.DOTALL):
                    return color_name
                if re.search(pattern_b, text, flags=re.IGNORECASE | re.DOTALL):
                    return color_name
    return fallback


def _extract_image_index(text: str, images_count: int) -> int | None:
    if images_count <= 1:
        return 0

    if _contains_any(text, ("全部", "所有", "每张", "all images", "all slides")):
        return None

    match = re.search(r"第\s*(\d+)\s*张", text)
    if match:
        return int(match.group(1)) - 1

    for cn, number in _CN_NUM_MAP.items():
        if f"第{cn}张" in text:
            return number - 1

    if _contains_any(text, ("首图", "第一张", "封面", "first image", "first slide")):
        return 0

    return None


def _has_style_scoped_image_reference(text: str) -> bool:
    image_pattern = r"(第\s*\d+\s*张|第[一二三四五六七八九十]张|首图|第一张|封面|first image|first slide)"
    style_pattern = r"(圈|圈出|下划线|划线|高亮|highlight|circle|underline)"
    if not re.search(image_pattern, text, flags=re.IGNORECASE):
        return False
    return bool(
        re.search(image_pattern + r".{0,14}" + style_pattern, text, flags=re.IGNORECASE)
        or re.search(style_pattern + r".{0,14}" + image_pattern, text, flags=re.IGNORECASE)
    )


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(token.lower() in lowered for token in tokens)


def _normalize_for_match(text: str) -> str:
    normalized_chars: list[str] = []
    for char in text:
        if char.isalnum() or _is_cjk(char):
            normalized_chars.append(char.lower())
    return "".join(normalized_chars)


def _keyword_norm_match(text_norm: str, keyword_norm: str) -> bool:
    if text_norm == keyword_norm:
        return True
    if not text_norm or not keyword_norm:
        return False
    if not text_norm.isascii() or not keyword_norm.isascii():
        return False

    # Handle singular/plural drift in English OCR ("skill" vs "skills").
    if text_norm.endswith("s") and text_norm[:-1] == keyword_norm:
        return True
    if keyword_norm.endswith("s") and keyword_norm[:-1] == text_norm:
        return True
    return False


def _contains_cjk(text: str) -> bool:
    return any(_is_cjk(char) for char in text)


def _word_compatible_with_keyword(text_norm: str, keyword_norm: str) -> bool:
    if not text_norm:
        return False
    keyword_has_cjk = _contains_cjk(keyword_norm)
    keyword_has_ascii = any(char.isascii() and char.isalnum() for char in keyword_norm)
    token_has_cjk = _contains_cjk(text_norm)
    token_has_ascii = any(char.isascii() and char.isalnum() for char in text_norm)

    if keyword_has_cjk and not keyword_has_ascii:
        return token_has_cjk
    if keyword_has_ascii and not keyword_has_cjk:
        return token_has_ascii
    return True


def _is_cjk(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"


def _dedupe_boxes(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    unique: list[tuple[int, int, int, int]] = []
    for box in boxes:
        if box not in unique:
            unique.append(box)
    return unique
