"""
DuckDuckGo web search provider.

Uses the unofficial DuckDuckGo search library — no API key required,
completely free. Results include title, URL, and a text snippet.

Requires:
    pip install duckduckgo-search
"""
from __future__ import annotations

import logging

from .base import WebSearchProvider, WebSearchResult

logger = logging.getLogger(__name__)


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
            from duckduckgo_search import DDGS  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "duckduckgo-search is required for DuckDuckGoProvider. "
                "Install it with: pip install duckduckgo-search"
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
        results: list[WebSearchResult] = []

        with self._DDGS() as ddgs:
            for item in ddgs.text(
                query,
                region=self._region,
                safesearch=self._safesearch,
                max_results=num_results,
            ):
                results.append(
                    WebSearchResult(
                        title=item.get("title", ""),
                        url=item.get("href", ""),
                        content=item.get("body", ""),
                        score=None,  # DDG does not expose a relevance score
                    )
                )

        logger.debug(
            "DuckDuckGoProvider returned %d results for query: %s",
            len(results),
            query[:80],
        )
        return results
