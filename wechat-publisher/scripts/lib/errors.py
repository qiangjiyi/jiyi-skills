"""Common error type and constants for wechat-publisher."""

from __future__ import annotations


class PublishError(Exception):
    """Raised for any user-facing error during publish."""


DEFAULT_API_BASE = "https://api.weixin.qq.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# newspic-specific limits
TITLE_MAX_CHARS = 20
CONTENT_MAX_CHARS = 1200
MAX_IMAGES = 20
