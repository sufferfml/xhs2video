from xhs_video_mcp.video_maker import _resolve_image_durations, _resolve_transition_for_durations
from xhs_video_mcp.video_plan_workflow import _extract_structured_plan
from xhs_video_mcp.video_render_workflow import _resolve_durations


def test_extract_structured_plan_supports_duration_list() -> None:
    payload = {
        "selected_indices": [1, 2],
        "render_plan": {
            "duration_per_image_list": [5, 3],
            "bgm": "none",
            "style_prompt": "style",
            "ffmpeg_goal": "goal",
        },
    }
    plan = _extract_structured_plan(
        payload=payload,
        images_count=3,
        available_bgm=["demo.mp3"],
    )
    assert plan.selected_indices == [1, 2]
    assert plan.duration_per_image == 5
    assert plan.duration_per_image_list == [5, 3]


def test_extract_structured_plan_rejects_invalid_duration_list_length() -> None:
    payload = {
        "selected_indices": [1, 2],
        "render_plan": {"duration_per_image_list": [5], "bgm": "none"},
    }
    try:
        _extract_structured_plan(
            payload=payload,
            images_count=3,
            available_bgm=["demo.mp3"],
        )
    except ValueError:
        return
    raise AssertionError("Expected ValueError for mismatched duration_per_image_list length")


def test_resolve_durations_prefers_duration_list_in_plan() -> None:
    duration, values = _resolve_durations(
        plan={"render_plan": {"duration_per_image": 3, "duration_per_image_list": [5, 3]}},
        duration_override=None,
        images_count=2,
    )
    assert duration == 5
    assert values == [5.0, 3.0]


def test_resolve_image_durations_validates_list_length() -> None:
    try:
        _resolve_image_durations(
            image_count=2,
            duration_per_image=None,
            duration_per_image_list=[4],
            default_duration=3.0,
        )
    except ValueError:
        return
    raise AssertionError("Expected ValueError for mismatched duration_per_image_list length")


def test_transition_uses_shortest_image_duration() -> None:
    transition = _resolve_transition_for_durations(transition_duration=2.0, image_durations=[5.0, 1.2])
    assert transition < 1.2
