"""
DuckDuckGo web search provider.

Uses the unofficial DuckDuckGo search library — no API key required,
completely free. Results include title, URL, and a text snippet.

Requires:
    pip install ddgs
"""
from __future__ import annotations

import logging

from .base import WebSearchProvider, WebSearchResult

logger = logging.getLogger(__name__)

# The ddgs library logs individual engine failures at INFO level internally
# (e.g. Wikipedia ConnectError). Silence them so they don't pollute app logs;
# real failures that bubble up are caught in DuckDuckGoProvider.search().
logging.getLogger("ddgs").setLevel(logging.WARNING)


class DuckDuckGoProvider(WebSearchProvider):
    """
    Web search backed by DuckDuckGo (no API key required).

    Args:
        region: DuckDuckGo region code, e.g. "wt-wt" (worldwide), "us-en", "ar-ar".
        safesearch: "on", "moderate", or "off".
    """

    def __init__(
        self,
        region: str = "wt-wt",
        safesearch: str = "moderate",
    ) -> None:
        try:
            from ddgs import DDGS  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "ddgs is required for DuckDuckGoProvider. "
                "Install it with: pip install ddgs"
            ) from exc

        self._DDGS = DDGS
        self._region = region
        self._safesearch = safesearch

    def search(self, query: str, num_results: int = 5) -> list[WebSearchResult]:
        """
        Search the web via DuckDuckGo and return cleaned results.

        Args:
            query: The search query.
            num_results: Maximum number of results to return.

        Returns:
            List of WebSearchResult ordered by DuckDuckGo's relevance.
        """
        try:
            raw = self._DDGS().text(
                query,
                region=self._region,
                safesearch=self._safesearch,
                max_results=num_results,
            )
        except Exception as exc:
            logger.warning("DuckDuckGo search failed for query %r: %s", query[:80], exc)
            return []

        results = [
            WebSearchResult(
                title=item.get("title", ""),
                url=item.get("href", ""),
                content=item.get("body", ""),
                score=None,  # DDG does not expose a relevance score
            )
            for item in (raw or [])
        ]

        logger.debug(
            "DuckDuckGoProvider returned %d results for query: %s",
            len(results),
            query[:80],
        )
        return results
