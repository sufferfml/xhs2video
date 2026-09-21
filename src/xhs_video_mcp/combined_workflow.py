"""Combined workflow for one-message Telegram input (URL + directives)."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from .safety import public_url
from .image_workflow import extract_xhs_url, run_workflow as run_image_workflow
from .video_plan_workflow import run_workflow as run_plan_workflow
from .video_render_workflow import run_workflow as run_render_workflow


_SEPARATOR_PATTERNS = (
    r"\n\s*[-=/#]{3,}\s*\n",
    r"\n\s*(?:分镜|分镜要求|剪辑要求|视频要求)\s*[:：]\s*\n?",
)

_INLINE_DIRECTIVE_HINTS = (
    "选",
    "选择",
    "图片",
    "第",
    "秒",
    "时长",
    "共",
    "总时长",
    "音乐",
    "bgm",
    "圈",
    "下划线",
    "划线",
    "高亮",
    "关键词",
    "关键字",
    "剪辑",
    "分镜",
    "视频",
)


def _looks_like_model_plan(payload: dict[str, Any]) -> bool:
    if "selected_indices" in payload:
        return True
    render_plan = payload.get("render_plan")
    return isinstance(render_plan, dict) and "selected_indices" in render_plan


def _extract_model_plan_json(directives: str) -> str | None:
    text = (directives or "").strip()
    if not text:
        return None

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if not _looks_like_model_plan(payload):
            continue
        return text[index: index + end]
    return None


def _extract_directives_block(
    message_text: str,
    url: str | None = None,
) -> tuple[str | None, str | None]:
    """Extract directives after a strong separator marker."""
    text = (message_text or "").strip()
    if not text:
        return None, None

    for pattern in _SEPARATOR_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        directives = text[match.end():].strip()
        if directives:
            return directives, match.group(0).strip()

    inline_directives = _extract_inline_directives(text, url)
    if inline_directives:
        return inline_directives, "inline"
    return None, None


def _extract_inline_directives(message_text: str, url: str | None) -> str | None:
    """Extract one-shot directives when URL and requirements are in one sentence."""
    if not message_text or not url:
        return None

    candidate = message_text.replace(url, " ").strip()
    if not candidate:
        return None
    candidate = re.sub(r"^[\s,，。;；:：|/\\-]+", "", candidate).strip()
    if not candidate:
        return None

    lowered = candidate.lower()
    has_hint = any(token in lowered for token in _INLINE_DIRECTIVE_HINTS)
    has_structure = bool(
        re.search(r"\d+\s*(?:秒|s\b)|[\"“”'‘’].+?[\"“”'‘’]", candidate, flags=re.IGNORECASE)
    )
    if has_hint and has_structure:
        return candidate
    return None


async def run_workflow(message_text: str) -> dict[str, Any]:
    """Run Step-1 only or one-shot Step-1->Step-2->Step-3 based on separators."""
    text = (message_text or "").strip()
    url = extract_xhs_url(text)
    if not url:
        return {"ok": False, "error": "No Xiaohongshu URL found in message_text"}

    directives, separator = _extract_directives_block(text, url=url)
    image_result = await run_image_workflow(url=url)
    if not image_result.get("ok"):
        return image_result

    if not directives:
        return {
            "ok": True,
            "mode": "step1_only",
            "url": public_url(url),
            "separator_detected": False,
            "image_result": image_result,
        }

    download_dir_text = image_result.get("download_dir")
    if not isinstance(download_dir_text, str) or not download_dir_text.strip():
        return {
            "ok": False,
            "error": "Step-1 succeeded but download_dir is missing.",
            "url": public_url(url),
            "image_result": image_result,
        }
    download_dir = Path(download_dir_text)
    model_plan_json = _extract_model_plan_json(directives)
    if not model_plan_json:
        return {
            "ok": True,
            "mode": "split_but_plan_failed",
            "url": public_url(url),
            "separator_detected": True,
            "separator": separator,
            "directives": directives,
            "image_result": image_result,
            "plan_error": (
                "Step-2 now requires model-structured JSON plan. "
                "No valid JSON object with selected_indices was found in directives."
            ),
        }

    try:
        plan_result = run_plan_workflow(
            download_dir=download_dir,
            request_text=directives,
            model_plan_json=model_plan_json,
        )
    except Exception as exc:
        return {
            "ok": True,
            "mode": "split_but_plan_failed",
            "url": public_url(url),
            "separator_detected": True,
            "separator": separator,
            "directives": directives,
            "image_result": image_result,
            "plan_error": str(exc),
        }

    if not plan_result.get("ok"):
        return {
            "ok": False,
            "error": plan_result.get("error", "Step-2 planning failed."),
            "url": public_url(url),
            "separator_detected": True,
            "separator": separator,
            "directives": directives,
            "image_result": image_result,
            "plan_result": plan_result,
        }

    render_result = await run_render_workflow(
        download_dir=download_dir,
        plan_path=Path(plan_result["plan_path"]),
    )
    if not render_result.get("ok"):
        return {
            "ok": False,
            "error": render_result.get("error", "Step-3 render failed."),
            "url": public_url(url),
            "separator_detected": True,
            "separator": separator,
            "directives": directives,
            "image_result": image_result,
            "plan_result": plan_result,
            "render_result": render_result,
        }

    return {
        "ok": True,
        "mode": "one_shot",
        "url": public_url(url),
        "separator_detected": separator not in {None, "inline"},
        "separator": separator,
        "directives": directives,
        "image_result": image_result,
        "plan_result": plan_result,
        "render_result": render_result,
        "video_path": render_result.get("video_path"),
        "result_path": render_result.get("result_path"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Combined Telegram workflow: parse one message with XHS URL and optional "
            "directives block, then run Step-1 only or full Step-1->2->3."
        )
    )
    parser.add_argument(
        "--text",
        required=True,
        help="Full Telegram message text (URL only, or URL + separator + directives).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        result = asyncio.run(run_workflow(message_text=args.text))
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}

    print(json.dumps(result, ensure_ascii=False))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
