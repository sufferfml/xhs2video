"""Xiaohongshu post parser using Playwright.

SPDX-License-Identifier: GPL-3.0-only
CDN token extraction and URL construction follow JoeanAmier/XHS-Downloader
(GPLv3). Adapted for Playwright, validation and bounded downloads; see
THIRD_PARTY_NOTICES.md for the upstream revision and modification details.
"""

import asyncio
from collections.abc import Iterable
from io import BytesIO
import re
import shutil
import logging
import tempfile
import uuid
from pathlib import Path
from dataclasses import dataclass

import httpx
from PIL import Image, UnidentifiedImageError
from playwright.async_api import async_playwright, Page, Browser

from .config import config
from .safety import validate_url

logger = logging.getLogger(__name__)


@dataclass
class XHSPost:
    """Parsed Xiaohongshu post data."""

    post_id: str
    title: str
    image_urls: list[str]
    image_paths: list[Path]  # Local paths after download
    temp_dir: Path  # Unique to this parse invocation


class XHSParser:
    """Parser for Xiaohongshu posts using Playwright."""

    # Common User-Agent to avoid detection
    USER_AGENT = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/16.0 Mobile/15E148 Safari/604.1"
    )

    def __init__(self):
        self.browser: Browser | None = None
        self.download_dir: Path | None = None

    @staticmethod
    def _normalize_image_url(url: str) -> str:
        """Normalize escaped/image URLs and enforce https."""
        normalized = (
            url.replace("\\u002F", "/")
            .replace("\\/", "/")
            .replace("\\u0026", "&")
            .strip()
            .strip('"')
            .strip("'")
        )
        if normalized.startswith("http://"):
            normalized = "https://" + normalized[len("http://"):]
        return normalized

    @staticmethod
    def _image_asset_key(url: str) -> str:
        """Canonical key for deduplicating image variants."""
        normalized = XHSParser._normalize_image_url(url)
        normalized = re.sub(r"^https?://", "", normalized)
        normalized = re.sub(r"\?.*$", "", normalized)
        return normalized

    @staticmethod
    def _is_candidate_content_image(url: str) -> bool:
        """Whether this URL likely points to a real post image."""
        lower = url.lower()
        if "xhscdn.com" not in lower:
            return False
        if "/avatar/" in lower or "/emoji/" in lower:
            return False
        if re.fullmatch(r"https?://sns-webpic[^/]*\.xhscdn\.com/?", lower):
            return False
        if "sns-webpic" in lower and (
            "!h5_" in lower
            or "1040g" in lower
            or re.search(r"\.(jpg|jpeg|png|webp)(?:$|\?)", lower)
        ):
            return True
        if "!h5_1080" in lower or "!h5_" in lower:
            return True
        if "/images/" in lower and ("jpg" in lower or "jpeg" in lower or "png" in lower):
            return True
        return False

    @staticmethod
    def _score_image_url(url: str) -> int:
        """Priority score for selecting higher-quality content images."""
        lower = url.lower()
        score = 0
        if "sns-webpic" in lower:
            score += 120
        if "!h5_1080" in lower:
            score += 240
        elif "!h5_" in lower:
            score += 160
        if "/avatar/" in lower or "/emoji/" in lower:
            score -= 300
        return score

    @staticmethod
    def _extract_urls_from_srcset(srcset: str | None) -> list[str]:
        """Extract candidate URLs from a srcset attribute."""
        if not srcset:
            return []

        urls: list[str] = []
        for item in srcset.split(","):
            candidate = item.strip().split(" ")[0]
            if candidate:
                urls.append(candidate)
        return urls

    def _select_best_image_url(self, urls: Iterable[str]) -> str | None:
        """Pick the highest-quality URL candidate from a URL list."""
        scored: list[tuple[int, str]] = []
        for raw_url in urls:
            normalized = self._normalize_image_url(raw_url)
            if not normalized:
                continue
            if "xhscdn.com" not in normalized:
                continue
            scored.append((self._score_image_url(normalized), normalized))

        if not scored:
            return None

        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    @staticmethod
    def _extract_note_from_state(state: dict) -> dict | None:
        """Extract note payload from mobile or PC INITIAL_STATE structures."""
        if not isinstance(state, dict):
            return None

        # Mobile structure: noteData.data.noteData
        note_data = state.get("noteData", {})
        if isinstance(note_data, dict):
            data = note_data.get("data", {})
            if isinstance(data, dict):
                note = data.get("noteData")
                if isinstance(note, dict):
                    return note

        # PC structure: note.noteDetailMap[*].note
        note_root = state.get("note", {})
        if isinstance(note_root, dict):
            note_detail_map = note_root.get("noteDetailMap", {})
            if isinstance(note_detail_map, dict):
                for detail in note_detail_map.values():
                    if not isinstance(detail, dict):
                        continue
                    note = detail.get("note")
                    if isinstance(note, dict):
                        return note

        return None

    @staticmethod
    def _extract_image_token(url: str) -> str | None:
        """Extract CDN token from image URL, following XHS-Downloader approach."""
        normalized = XHSParser._normalize_image_url(url)
        if not normalized:
            return None

        cleaned = normalized.split("?", 1)[0]
        if "ci.xiaohongshu.com/" in cleaned:
            token = cleaned.split("ci.xiaohongshu.com/", 1)[1]
        else:
            parts = cleaned.split("/")
            if len(parts) > 5:
                token = "/".join(parts[5:])
            elif len(parts) > 3:
                token = "/".join(parts[3:])
            else:
                return None

        token = token.split("!", 1)[0].strip("/")
        return token or None

    @staticmethod
    def _build_image_url_from_token(token: str, image_format: str) -> str:
        """Build final image URL from CDN token."""
        if image_format == "auto":
            return f"https://sns-img-bd.xhscdn.com/{token}"
        return f"https://ci.xiaohongshu.com/{token}?imageView2/format/{image_format}"

    async def _extract_image_urls_from_initial_state(self, page: Page) -> list[str]:
        """Extract exact post image URLs from window.__INITIAL_STATE__ when available."""
        try:
            state = await page.evaluate("() => window.__INITIAL_STATE__")
        except Exception:
            return []

        note = self._extract_note_from_state(state)
        if not isinstance(note, dict):
            return []

        image_list = note.get("imageList", [])
        if not isinstance(image_list, list):
            return []

        ordered_urls: list[str] = []
        seen_keys: set[str] = set()
        use_ci_mode = config.image_source_mode == "ci"

        for image_item in image_list:
            if not isinstance(image_item, dict):
                continue

            candidates: list[str] = []
            for key in ("urlDefault", "url"):
                image_url = image_item.get(key)
                if isinstance(image_url, str):
                    candidates.append(image_url)

            info_list = image_item.get("infoList", [])
            if isinstance(info_list, list):
                for info in info_list:
                    if not isinstance(info, dict):
                        continue
                    info_url = info.get("url")
                    if isinstance(info_url, str):
                        candidates.append(info_url)

            if use_ci_mode:
                token = None
                for candidate in candidates:
                    token = self._extract_image_token(candidate)
                    if token:
                        break
                if token:
                    key = f"token:{token}"
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    ordered_urls.append(
                        self._build_image_url_from_token(token, config.image_format)
                    )
                    continue

            best_url = self._select_best_image_url(candidates)
            if best_url:
                best_url = re.sub(r"\?.*$", "", best_url)
                key = self._image_asset_key(best_url)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                ordered_urls.append(best_url)

        return ordered_urls

    @staticmethod
    def extract_post_id(url: str) -> str | None:
        """Extract post ID from various XHS URL formats."""
        # Desktop format: https://www.xiaohongshu.com/explore/xxxxx
        # Mobile format: https://www.xiaohongshu.com/discovery/item/xxxxx
        # Share links (xhslink.com) should be resolved first; they do not
        # directly contain the final post ID.

        patterns = [
            r"xiaohongshu\.com/explore/([a-zA-Z0-9]+)",
            r"xiaohongshu\.com/discovery/item/([a-zA-Z0-9]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

    @staticmethod
    def normalize_url(url: str) -> str:
        """Normalize XHS URL to a consistent format."""
        # Keep short share links unchanged so Playwright can follow redirects
        # and reach the canonical post URL.
        if "xhslink.com/" in url:
            return url

        # Keep direct explore/discovery links unchanged so query tokens like
        # xsec_token are preserved for private or anti-scrape guarded pages.
        if "xiaohongshu.com/explore/" in url or "xiaohongshu.com/discovery/item/" in url:
            return url

        post_id = XHSParser.extract_post_id(url)
        if post_id:
            return f"https://www.xiaohongshu.com/explore/{post_id}"
        return url

    async def _init_browser(self):
        """Initialize browser if not already initialized."""
        if self.browser is None:
            self._playwright = await async_playwright().start()
            self.browser = await self._playwright.chromium.launch(
                headless=config.headless
            )

    async def close(self):
        """Close the browser and playwright."""
        if self.browser:
            await self.browser.close()
            self.browser = None
        if hasattr(self, '_playwright') and self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def _close_login_popup(self, page: Page):
        """Try to close the login popup if it appears."""
        try:
            # Common close button selectors
            close_selectors = [
                'button[aria-label="close"]',
                '.close-button',
                '[class*="close"]',
                'svg[class*="close"]',
            ]

            for selector in close_selectors:
                try:
                    close_btn = page.locator(selector).first
                    if await close_btn.is_visible(timeout=1000):
                        await close_btn.click()
                        await asyncio.sleep(0.5)
                        return True
                except Exception:
                    continue

            # Try pressing Escape
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)

        except Exception:
            pass

        return False

    async def _extract_image_urls(
        self, page: Page, network_image_urls: list[str]
    ) -> list[str]:
        """Extract image URLs from the page."""
        candidates: list[str] = []

        # Wait for images to load
        await asyncio.sleep(2)

        # Try different selectors for images
        selectors = [
            'img[class*="note-image"]',
            'img[class*="swiper-slide"] img',
            '.note-content img',
            '.carousel img',
            'img[src*="xhscdn.com"]',
            'img[src*="xiaohongshu"]',
        ]

        for selector in selectors:
            try:
                images = await page.locator(selector).all()
                for img in images:
                    for attr in ("src", "data-src", "data-original"):
                        src = await img.get_attribute(attr)
                        if src:
                            candidates.append(src)
                    srcset = await img.get_attribute("srcset")
                    candidates.extend(self._extract_urls_from_srcset(srcset))
            except Exception:
                continue

        # Fallback: get all images and filter
        if not candidates:
            all_images = await page.locator("img").all()
            for img in all_images:
                src = await img.get_attribute("src")
                if src:
                    candidates.append(src)
                srcset = await img.get_attribute("srcset")
                candidates.extend(self._extract_urls_from_srcset(srcset))

        # Parse page HTML for escaped CDN URLs that are not mounted in <img>.
        try:
            html = await page.content()
            for match in re.finditer(r'https?://[^"\'\s>]+xhscdn\.com[^"\'\s>]*', html):
                candidates.append(match.group(0))
        except Exception:
            pass

        # Add network-level URLs collected during navigation.
        candidates.extend(network_image_urls)

        best_by_key: dict[str, tuple[int, int, str]] = {}
        for index, raw_url in enumerate(candidates):
            normalized = self._normalize_image_url(raw_url)
            normalized = re.sub(r"\?.*$", "", normalized)
            if not self._is_candidate_content_image(normalized):
                continue

            key = self._image_asset_key(normalized)
            score = self._score_image_url(normalized)
            existing = best_by_key.get(key)
            if existing is None or score > existing[0]:
                best_by_key[key] = (score, index, normalized)

        ordered = sorted(best_by_key.values(), key=lambda item: (-item[0], item[1]))
        return [item[2] for item in ordered]

    async def _extract_title(self, page: Page) -> str:
        """Extract post title from the page."""
        title_selectors = [
            'h1[class*="title"]',
            '.note-title',
            'meta[property="og:title"]',
            'title',
        ]

        for selector in title_selectors:
            try:
                if selector.startswith("meta"):
                    elem = page.locator(selector)
                    title = await elem.get_attribute("content")
                else:
                    elem = page.locator(selector).first
                    title = await elem.text_content()

                if title:
                    # Clean up title
                    title = title.strip()
                    title = re.sub(r'\s+', ' ', title)
                    return title[:100]  # Limit length
            except Exception:
                continue

        return "untitled"

    async def _download_images(
        self,
        image_urls: list[str],
        post_id: str,
        apply_size_filter: bool = True,
    ) -> list[Path]:
        """Stream bounded downloads into this invocation's private directory."""
        if len(image_urls) > config.max_images:
            raise ValueError(f"A post may contain at most {config.max_images} images.")
        assert self.download_dir is not None
        downloaded_paths: list[Path] = []
        total_bytes = 0
        async with httpx.AsyncClient(
            headers={"User-Agent": self.USER_AGENT},
            follow_redirects=False, timeout=30.0, trust_env=False,
        ) as client:
            for url in image_urls:
                try:
                    validate_url(url)
                    content = bytearray()
                    async with client.stream("GET", url) as response:
                        # Do not follow redirects into an unvalidated host.
                        response.raise_for_status()
                        if "image" not in response.headers.get("content-type", "").lower():
                            continue
                        async for chunk in response.aiter_bytes():
                            total_bytes += len(chunk)
                            if total_bytes > config.max_download_bytes:
                                raise ValueError("Post download byte limit exceeded.")
                            content.extend(chunk)
                            if len(content) > config.max_image_bytes:
                                raise ValueError("Image download byte limit exceeded.")
                    with Image.open(BytesIO(content)) as image:
                        width, height = image.size
                        image_format = image.format
                        if width * height > config.max_image_pixels:
                            raise ValueError("Image pixel limit exceeded.")
                        image.verify()
                    if apply_size_filter and (
                        width * height < config.min_image_area
                        or min(width, height) < config.min_image_short_side
                    ):
                        continue
                    ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(image_format)
                    if ext is None:
                        continue
                    file_path = self.download_dir / f"image_{len(downloaded_paths):03d}{ext}"
                    file_path.write_bytes(content)
                    downloaded_paths.append(file_path)
                except (httpx.HTTPError, UnidentifiedImageError, OSError):
                    # Error strings can include signed URLs; never log them.
                    logger.warning("An image download failed; no URL or query token logged.")
        return downloaded_paths

    async def parse(self, url: str) -> XHSPost:
        """Parse a Xiaohongshu post and download its images."""
        validate_url(url, post=True)
        config.ensure_directories()
        await self._init_browser()

        post_id = self.extract_post_id(url) or str(uuid.uuid4())[:8]
        normalized_url = self.normalize_url(url)

        context = await self.browser.new_context(
            user_agent=self.USER_AGENT,
            viewport={"width": 390, "height": 844},  # iPhone viewport
            device_scale_factor=3,
            service_workers="block",
        )

        # Block untrusted resource hosts and validate every main-frame redirect.
        async def route_request(route):
            request = route.request
            try:
                is_main = request.is_navigation_request() and request.frame.parent_frame is None
                validate_url(request.url, post=is_main)
            except ValueError:
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", route_request)
        page = await context.new_page()
        network_image_urls: list[str] = []

        def _on_request(request):
            request_url = request.url
            if "xhscdn.com" in request_url:
                network_image_urls.append(request_url)

        page.on("request", _on_request)

        try:
            # Navigate to the page
            try:
                await page.goto(normalized_url, wait_until="domcontentloaded", timeout=config.browser_timeout)
                validate_url(page.url, post=True)
            except Exception:
                raise RuntimeError("Unable to open this post within the supported URL and time limits.") from None

            # xhslink share URLs resolve to canonical post URLs after redirect.
            resolved_post_id = self.extract_post_id(page.url)
            if resolved_post_id:
                post_id = resolved_post_id

            # Try to close login popup
            await self._close_login_popup(page)

            # Extract data
            title = await self._extract_title(page)
            # Prefer exact note image list from page state; fallback to heuristics.
            image_urls = await self._extract_image_urls_from_initial_state(page)
            extracted_from_state = bool(image_urls)
            if not image_urls:
                image_urls = await self._extract_image_urls(page, network_image_urls)

            if not image_urls:
                raise ValueError("No images found. The post may be unavailable or require login.")

            # A distinct directory prevents cleanup races for the same post.
            self.download_dir = Path(tempfile.mkdtemp(prefix="job-", dir=config.temp_dir))
            image_paths = await self._download_images(
                image_urls=image_urls,
                post_id=post_id,
                apply_size_filter=not extracted_from_state,
            )

            if not image_paths:
                raise ValueError("Failed to download any supported post images.")

            return XHSPost(
                post_id=post_id,
                title=title,
                image_urls=image_urls,
                image_paths=image_paths,
                temp_dir=self.download_dir,
            )
        except BaseException:
            # Includes cancellation from the whole-parse deadline.
            if self.download_dir is not None:
                shutil.rmtree(self.download_dir, ignore_errors=True)
            raise

        finally:
            await context.close()


# Convenience function for single-use parsing
async def parse_xhs_url(url: str) -> XHSPost:
    """Parse a Xiaohongshu URL and return post data with downloaded images."""
    parser = XHSParser()
    try:
        return await asyncio.wait_for(parser.parse(url), timeout=config.parse_timeout)
    finally:
        await parser.close()
