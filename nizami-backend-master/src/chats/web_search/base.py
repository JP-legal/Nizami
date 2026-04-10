"""
Abstract web search provider interface.

All concrete providers must implement ``WebSearchProvider``.
Swap providers by changing ``WEB_SEARCH_PROVIDER`` in settings without
touching any other part of the codebase.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class WebSearchResult:
    """A single result returned by a web search provider."""

    title: str
    url: str
    content: str
    score: float | None = None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WebSearchResult title={self.title!r} url={self.url!r}>"


class WebSearchProvider(ABC):
    """
    Abstract base class for web search providers.

    Implementations must be stateless enough to be called from multiple
    threads simultaneously (the orchestrator submits them to a thread pool).
    """

    @abstractmethod
    def search(self, query: str, num_results: int = 5) -> list[WebSearchResult]:
        """
        Execute a web search and return a list of results.

        Args:
            query: The search query string.
            num_results: Maximum number of results to return.

        Returns:
            List of WebSearchResult objects, ordered by relevance.

        Raises:
            Any exception on hard failure — callers are responsible for
            catching and handling errors gracefully.
        """
        ...
