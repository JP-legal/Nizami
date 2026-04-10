"""
URL content extractor.

Fetches a URL with httpx (async) and returns clean text via BeautifulSoup.
The result is a WebSearchResult so it plugs directly into the same
context-formatting helpers used for web search results.

Requires: httpx, beautifulsoup4 (both already in requirements.txt).
"""
from __future__ import annotations

import asyncio
import logging

import httpx
from bs4 import BeautifulSoup

from .base import WebSearchResult

logger = logging.getLogger(__name__)

# Separate connect vs. read timeouts so a slow server body doesn't block
# indefinitely even when the TCP handshake was fast.
_TIMEOUT = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)
_MAX_CONTENT_CHARS = 8_000
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Nizami/1.0; +https://nizami.ai)"
}


async def fetch_url_content(url: str) -> WebSearchResult | None:
    """
    Fetch *url* asynchronously and return a WebSearchResult with its text.

    Returns None on any network or parse failure so callers can continue
    safely without crashing the conversation flow.

    Args:
        url: The fully-qualified URL to fetch (must start with http/https).

    Returns:
        WebSearchResult with title, url, and cleaned page text, or None.
    """
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            headers=_HEADERS,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
    except httpx.TimeoutException:
        logger.warning("URL fetch timed out: %s", url)
        return None
    except Exception:
        logger.warning("URL fetch failed: %s", url, exc_info=True)
        return None

    try:
        soup = BeautifulSoup(html, "html.parser")

        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else url

        # Strip noise elements before extracting text
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # Prefer <article> or <main> for focused content; fall back to <body>
        main = soup.find("article") or soup.find("main") or soup.find("body")
        content = (main or soup).get_text(separator="\n", strip=True)

        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS] + "\n...[content truncated]"

        logger.debug("Fetched %d chars from %s", len(content), url)
        return WebSearchResult(title=title, url=url, content=content)

    except Exception:
        logger.warning("URL parse failed: %s", url, exc_info=True)
        return None


async def fetch_urls(urls: list[str]) -> list[WebSearchResult]:
    """Fetch multiple URLs concurrently and return successful results."""
    results = await asyncio.gather(*[fetch_url_content(url) for url in urls])
    return [r for r in results if r is not None]
