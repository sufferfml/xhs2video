"""Input boundaries for a local, single-user application."""

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


POST_HOSTS = {"xiaohongshu.com", "www.xiaohongshu.com", "xhslink.com", "www.xhslink.com"}
ASSET_DOMAINS = {"xiaohongshu.com", "xhscdn.com", "xhslink.com"}


def validate_url(url: str, *, post: bool = False) -> str:
    """Reject non-HTTPS, credentials, unexpected ports and lookalike domains."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        valid_host = host in POST_HOSTS if post else any(
            host == domain or host.endswith("." + domain) for domain in ASSET_DOMAINS
        )
        if (parts.scheme != "https" or not valid_host or parts.username is not None
                or parts.password is not None or parts.port not in (None, 443)
                or any(ord(c) < 33 for c in url) or "\\" in url):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Only HTTPS URLs on supported Xiaohongshu hosts are allowed.") from None
    return url


def public_url(url: str) -> str:
    """Do not echo signed query strings, credentials or fragments to logs/results."""
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))
    except ValueError:
        return "[invalid URL]"


def require_job_dir(path: Path | None) -> Path:
    if path is None or str(path).strip().lower() == "latest":
        raise ValueError("Pass the explicit download_dir returned by Step-1; 'latest' is disabled.")
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError("The selected job directory does not exist.")
    return resolved


def within_job(path: Path, job_dir: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(job_dir.resolve()) or not resolved.is_file():
        raise ValueError("Selected file must exist inside the chosen job directory.")
    return resolved
