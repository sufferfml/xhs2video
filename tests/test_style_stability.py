from xhs_video_mcp.style_engine import (
    _pick_primary_keyword_box,
    _resolve_ocr_psm_candidates,
    _word_compatible_with_keyword,
    parse_style_prompt,
)
from xhs_video_mcp.video_maker import (
    _build_handdrawn_underline_points,
    _expand_box_for_underline,
)


def test_parse_style_prompt_keeps_local_action_and_color_mapping() -> None:
    prompt = (
        "把“致命伤”用蓝色画笔圈出来，把“claude skill”用红色画笔画上下划线。"
    )
    plan = parse_style_prompt(prompt, images_count=2)
    actions = [(item.kind, item.keyword, item.color) for item in plan.actions]
    assert ("circle_keyword", "致命伤", "blue") in actions
    assert ("underline_keyword", "claude skill", "red") in actions


def test_pick_primary_keyword_box_prefers_top_left_on_equal_score() -> None:
    boxes = [
        (400, 300, 500, 380),
        (120, 140, 220, 220),
    ]
    selected, reason = _pick_primary_keyword_box(boxes)
    assert selected == (120, 140, 220, 220)
    assert reason == "covers_most_candidates_then_largest_area"


def test_resolve_ocr_psm_candidates_is_deduplicated() -> None:
    assert _resolve_ocr_psm_candidates(4) == [4, 3, 6]
    assert _resolve_ocr_psm_candidates(11) == [11, 4, 3, 6]


def test_word_compatible_with_keyword_respects_language_type() -> None:
    assert _word_compatible_with_keyword("致命", "致命伤")
    assert not _word_compatible_with_keyword("skill", "致命伤")
    assert _word_compatible_with_keyword("skills", "claudeskill")
    assert not _word_compatible_with_keyword("致命", "claudeskill")


def test_handdrawn_underline_is_subtle_and_not_flat() -> None:
    box = _expand_box_for_underline((120, 860, 760, 980), frame_width=1080, frame_height=1920)
    assert box is not None
    points = _build_handdrawn_underline_points(
        box=box,
        seed=7,
        frame_width=1080,
        frame_height=1920,
    )
    ys = [point[1] for point in points]
    span = max(ys) - min(ys)
    assert span > 1.2
    assert span < 9.0
