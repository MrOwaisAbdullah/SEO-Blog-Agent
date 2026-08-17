import logging
import socket
import tempfile
from ipaddress import ip_address
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from agents import function_tool

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}


def _url_is_safe_public(url: str) -> Optional[str]:
    """Returns an error message if `url` should not be navigated to, None if it's
    fine. This tool hands an LLM-directed headless browser the ability to fetch
    whatever URL the agent decides to visit -- without a check like this, that is
    a straightforward SSRF vector (a prompt-injected or simply mistaken agent
    could point the browser at an internal network service instead of the public
    site it meant to screenshot). Blocks non-http(s) schemes and any hostname that
    resolves to a private/loopback/link-local/reserved address."""
    try:
        parsed = urlparse(url)
    except Exception:
        return "URL could not be parsed."
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return f"Scheme '{parsed.scheme}' is not allowed; only http/https."
    hostname = parsed.hostname
    if not hostname:
        return "URL has no hostname."
    try:
        resolved = socket.gethostbyname(hostname)
        addr = ip_address(resolved)
    except Exception as e:
        return f"Could not resolve hostname '{hostname}': {e}"
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast:
        return f"'{hostname}' resolves to a non-public address ({resolved}); refusing to navigate there."
    return None


@function_tool
async def capture_screenshot_tool(url: str, full_page: bool = False, wait_ms: int = 2000) -> Dict[str, Any]:
    """
    Captures a real screenshot of a PUBLIC web page with a headless browser
    (Playwright/Chromium) -- use this to illustrate a specific real tool's/
    product's actual interface in a technical post. A genuine screenshot builds
    more trust than a generic stock photo when the post is specifically about
    that tool.

    HARD REQUIREMENT: only call this with a URL that has already been verified as
    real by an earlier pipeline step (e.g. one of the post's own External Source
    Links) -- never a URL you construct, guess, or recall from training data. Same
    rule as internal links: a plausible-looking invented URL is worse than no
    screenshot at all.

    Scope limit: this cannot log in anywhere -- it only sees what a logged-out
    visitor sees. Not useful for an authenticated dashboard, and deliberately so:
    a public-only scope means there is no realistic risk of a credential or
    private data appearing in the captured image, so no redaction step is needed
    here (compare to a system that does capture authenticated pages, which must
    redact secrets before the image is ever used).

    Returns {"image_url": <local temp PNG path>, "alt_text": ..., "source_url": url}
    on success -- image_url is a local file path, the same shape
    get_stock_image_tool/generate_image_tool already return, so it can be handed
    straight to the same Sanity-upload path. Returns {"error": ...} on failure;
    the caller should fall back to get_stock_image_tool rather than treat this as
    fatal.
    """
    safety_error = _url_is_safe_public(url)
    if safety_error:
        logger.warning(f"capture_screenshot_tool: refused '{url}': {safety_error}")
        return {"error": safety_error}

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"error": "playwright is not installed in this environment."}

    local_path = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                await page.goto(url, timeout=20000, wait_until="load")
                await page.wait_for_timeout(wait_ms)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                    local_path = tmp.name
                await page.screenshot(path=local_path, full_page=full_page)
            finally:
                await browser.close()
    except Exception as e:
        logger.error(f"capture_screenshot_tool: failed to capture '{url}': {e}")
        return {"error": f"Screenshot capture failed: {e}"}

    hostname = urlparse(url).hostname or url
    logger.info(f"capture_screenshot_tool: captured '{url}' -> {local_path}")
    return {
        "image_url": local_path,
        "alt_text": f"Screenshot of {hostname}",
        "source_url": url,
    }
