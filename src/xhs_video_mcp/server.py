"""MCP Server for XHS Video Generation."""

import shutil

from mcp.server.fastmcp import FastMCP

from .safety import public_url
from .config import config
from .xhs_parser import parse_xhs_url
from .video_maker import create_video_async


# Create MCP server instance
mcp = FastMCP(
    name="xhs-video",
    instructions="Create short videos from Xiaohongshu posts with BGM",
)


@mcp.tool()
async def create_video_from_xhs(
    url: str,
    bgm: str = "random",
    duration_per_image: int = 3,
    style_prompt: str = "",
) -> dict:
    """
    Create a short video from a Xiaohongshu post.

    Downloads images from the XHS post, adds background music,
    and generates a vertical (9:16) video suitable for TikTok/Reels.

    Args:
        url: Xiaohongshu post URL (supports various formats including share links)
        bgm: BGM file name from bgm folder, or "random" to pick randomly.
             Set to "none" to create video without music.
        duration_per_image: How many seconds each image should be displayed (default: 3)
        style_prompt: Natural-language style instructions for image annotation.
            Example: 在第一张图里把"马云"蓝色高亮并红笔圈出来

    Returns:
        A dict containing:
        - video_path: Full path to the generated video file
        - images_count: Number of images in the video
        - duration: Total video duration in seconds
        - bgm_used: Name of the BGM file used (or null if none)
        - title: Title of the original XHS post
        - style_report: Style parsing/OCR diagnostics (or null when not used)
    """
    # Parse XHS URL and download images
    post = await parse_xhs_url(url)
    temp_dir = post.temp_dir

    try:
        if not post.image_paths:
            return {
                "error": "No images found in the post",
                "url": public_url(url),
            }

        # Determine BGM path
        bgm_path = None
        if bgm.lower() == "random":
            bgm_path = config.get_random_bgm()
        elif bgm.lower() != "none":
            bgm_path = config.get_bgm_by_name(bgm)

        # Generate video
        result = await create_video_async(
            image_paths=post.image_paths,
            bgm_path=bgm_path,
            duration_per_image=duration_per_image,
            output_filename=f"xhs_{post.post_id}_{post.temp_dir.name}",
            style_prompt=style_prompt,
        )

        return {
            "video_path": str(result.video_path),
            "images_count": result.images_count,
            "duration": result.duration,
            "bgm_used": result.bgm_used,
            "title": post.title,
            "style_report": result.style_report,
        }
    finally:
        # Always cleanup temp images, even if rendering fails.
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


@mcp.tool()
async def list_bgm_files() -> dict:
    """
    List all available BGM files in the music folder.

    Returns:
        A dict containing:
        - bgm_files: List of available BGM file names
        - bgm_dir: Path to the BGM directory
    """
    config.ensure_directories()
    bgm_files = [
        f.name for f in config.bgm_dir.iterdir()
        if f.is_file() and f.suffix.lower() in config.supported_audio_formats
    ]

    return {
        "bgm_files": sorted(bgm_files),
        "bgm_dir": str(config.bgm_dir),
        "count": len(bgm_files),
    }


@mcp.tool()
async def get_video_config() -> dict:
    """
    Get current video generation configuration.

    Returns:
        A dict with current settings for video resolution, fps, etc.
    """
    return {
        "resolution": f"{config.video.resolution[0]}x{config.video.resolution[1]}",
        "aspect_ratio": "9:16 (portrait)",
        "fps": config.video.fps,
        "default_duration_per_image": config.video.duration_per_image,
        "transition_duration": config.video.transition_duration,
        "audio_fade_out_duration": config.video.audio_fade_out_duration,
        "output_format": config.video.output_format,
        "output_dir": str(config.output_dir),
    }


def main():
    """Entry point for the MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
