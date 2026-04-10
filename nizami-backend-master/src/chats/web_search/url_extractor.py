"""
URL content extractor.

Fetches a URL with requests and returns clean text via BeautifulSoup.
The result is a WebSearchResult so it plugs directly into the same
context-formatting helpers used for web search results.

No external API needed — uses only packages already in requirements.txt
(requests, beautifulsoup4).
"""
from __future__ import annotations

import logging

import requests
from bs4 import BeautifulSoup

from .base import WebSearchResult

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 10
_MAX_CONTENT_CHARS = 8_000
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; Nizami/1.0; +https://nizami.ai)"
    )
}


def fetch_url_content(url: str) -> WebSearchResult | None:
    """
    Fetch *url* and return a WebSearchResult with its extracted text.

    Returns None on any network or parse failure so callers can continue
    safely without crashing the conversation flow.

    Args:
        url: The fully-qualified URL to fetch (must start with http/https).

    Returns:
        WebSearchResult with title, url, and cleaned page text, or None.
    """
    try:
        response = requests.get(url, timeout=_TIMEOUT_SEC, headers=_HEADERS)
        response.raise_for_status()
    except Exception:
        logger.warning("URL fetch failed: %s", url, exc_info=True)
        return None

    try:
        soup = BeautifulSoup(response.text, "html.parser")

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
