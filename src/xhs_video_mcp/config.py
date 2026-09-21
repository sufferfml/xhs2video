"""Configuration management for XHS Video MCP Server."""

import os
import sys
from pathlib import Path
from dataclasses import dataclass, field


def user_data_dir() -> Path:
    """Use a user-owned directory, never the installed package directory."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "xhs-video-mcp"


@dataclass
class VideoConfig:
    """Video generation configuration."""

    duration_per_image: int = 3  # seconds per image
    transition_duration: float = 0.5  # fade transition duration
    resolution: tuple[int, int] = (1080, 1920)  # width x height (9:16 portrait)
    fps: int = 30
    audio_fade_out_duration: float = 2.0  # seconds
    output_format: str = "mp4"
    codec: str = "libx264"
    audio_codec: str = "aac"


@dataclass
class Config:
    """Main configuration for the MCP server."""

    # Directories
    bgm_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("XHS_BGM_DIR", user_data_dir() / "bgm")
    ))
    output_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("XHS_OUTPUT_DIR", user_data_dir() / "output")
    ))
    temp_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("XHS_TEMP_DIR", user_data_dir() / "tmp")
    ))

    # Video settings
    video: VideoConfig = field(default_factory=VideoConfig)

    # Browser settings
    headless: bool = True
    browser_timeout: int = 30000  # milliseconds
    parse_timeout: int = 120
    max_images: int = 20
    max_image_bytes: int = 20 * 1024 * 1024
    max_download_bytes: int = 200 * 1024 * 1024
    max_image_pixels: int = 40_000_000
    max_video_seconds: int = 240
    ffmpeg_timeout: int = 180
    ocr_timeout: int = 30

    # Image filtering for XHS parsing.
    # Helps skip avatars/icons and keep only post-quality source images.
    min_image_area: int = field(default_factory=lambda: int(
        os.environ.get("XHS_MIN_IMAGE_AREA", "350000")
    ))
    min_image_short_side: int = field(default_factory=lambda: int(
        os.environ.get("XHS_MIN_IMAGE_SHORT_SIDE", "700")
    ))
    image_source_mode: str = field(default_factory=lambda: os.environ.get(
        "XHS_IMAGE_SOURCE_MODE", "ci"
    ).lower())
    image_format: str = field(default_factory=lambda: os.environ.get(
        "XHS_IMAGE_FORMAT", "jpeg"
    ).lower())

    # Supported audio formats for BGM
    supported_audio_formats: tuple[str, ...] = (".mp3", ".wav", ".m4a", ".aac", ".ogg")

    def __post_init__(self):
        """Normalize configuration without writing files during import."""
        self.bgm_dir = Path(self.bgm_dir).expanduser().resolve()
        self.output_dir = Path(self.output_dir).expanduser().resolve()
        self.temp_dir = Path(self.temp_dir).expanduser().resolve()

        if self.image_source_mode not in {"ci", "auto"}:
            self.image_source_mode = "ci"
        if self.image_format not in {"jpeg", "webp", "png", "auto"}:
            self.image_format = "jpeg"

    def ensure_directories(self) -> None:
        for path in (self.bgm_dir, self.output_dir, self.temp_dir):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)

    def get_random_bgm(self) -> Path | None:
        """Get a random BGM file from the bgm directory."""
        import random

        if not self.bgm_dir.exists():
            return None

        bgm_files = [
            f for f in self.bgm_dir.iterdir()
            if f.is_file() and f.suffix.lower() in self.supported_audio_formats
        ]

        if not bgm_files:
            return None

        return random.choice(bgm_files)

    def get_bgm_by_name(self, name: str) -> Path | None:
        """Get a specific BGM file by name (with or without extension)."""
        if not self.bgm_dir.exists():
            return None
        # Try exact match first
        for f in self.bgm_dir.iterdir():
            if f.is_file() and f.suffix.lower() in self.supported_audio_formats and (f.name == name or f.stem == name):
                return f
        return None


# Global config instance
config = Config()
