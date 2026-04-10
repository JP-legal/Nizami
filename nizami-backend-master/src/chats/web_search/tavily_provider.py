"""
Tavily web search provider.

Tavily is optimised for RAG use cases and returns cleaned, relevant
content rather than raw HTML snippets. It is the default provider.

Requires:
    pip install tavily-python
    TAVILY_API_KEY environment variable (or Django setting).
"""
from __future__ import annotations

import logging

from .base import WebSearchProvider, WebSearchResult

logger = logging.getLogger(__name__)


class TavilyProvider(WebSearchProvider):
    """
    Web search backed by the Tavily API.

    Args:
        api_key: Tavily API key. Obtain one at https://tavily.com.
        search_depth: "basic" (faster, cheaper) or "advanced" (more thorough).
        include_domains: Optional whitelist of domains to restrict results to.
        exclude_domains: Optional list of domains to exclude from results.
    """

    def __init__(
        self,
        api_key: str,
        search_depth: str = "basic",
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> None:
        try:
            from tavily import TavilyClient  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "tavily-python is required for TavilyProvider. "
                "Install it with: pip install tavily-python"
            ) from exc

        self._client = TavilyClient(api_key=api_key)
        self._search_depth = search_depth
        self._include_domains = include_domains or []
        self._exclude_domains = exclude_domains or []

    def search(self, query: str, num_results: int = 5) -> list[WebSearchResult]:
        """
        Search the web via Tavily and return cleaned results.

        Args:
            query: The search query.
            num_results: Maximum number of results (Tavily supports 1–10).

        Returns:
            List of WebSearchResult ordered by Tavily's relevance score.
        """
        kwargs: dict = {
            "query": query,
            "max_results": min(num_results, 10),
            "search_depth": self._search_depth,
        }
        if self._include_domains:
            kwargs["include_domains"] = self._include_domains
        if self._exclude_domains:
            kwargs["exclude_domains"] = self._exclude_domains

        response = self._client.search(**kwargs)

        results: list[WebSearchResult] = []
        for item in response.get("results", []):
            results.append(
                WebSearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    content=item.get("content", ""),
                    score=item.get("score"),
                )
            )

        logger.debug(
            "TavilyProvider returned %d results for query: %s",
            len(results),
            query[:80],
        )
        return results
