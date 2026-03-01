import logging
import re

import aiohttp

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_MAX_CONTENT_LENGTH = 4000
_FETCH_TIMEOUT = 15  # seconds


def extract_urls(text: str) -> list[str]:
    """Extract all URLs from text."""
    return _URL_RE.findall(text)


async def fetch_url_content(url: str) -> str | None:
    """Fetch clean markdown content from a URL via Jina Reader API."""
    jina_url = f"https://r.jina.ai/{url}"
    try:
        timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(jina_url) as resp:
                if resp.status != 200:
                    logger.warning("Jina fetch failed for %s: HTTP %d", url, resp.status)
                    return None
                content = await resp.text()
                if len(content) > _MAX_CONTENT_LENGTH:
                    content = content[:_MAX_CONTENT_LENGTH] + "\n[...truncated]"
                return content
    except Exception:
        logger.warning("Failed to fetch URL content: %s", url, exc_info=True)
        return None


async def augment_text_with_urls(text: str) -> str:
    """Extract URLs from text, fetch their content, and return augmented text for LLM."""
    urls = extract_urls(text)
    if not urls:
        return text

    parts = []
    for url in urls:
        content = await fetch_url_content(url)
        if content:
            parts.append(f"[Link content from {url}]:\n{content}")

    if not parts:
        return text

    return "\n\n".join(parts) + f"\n\nOriginal message:\n{text}"
