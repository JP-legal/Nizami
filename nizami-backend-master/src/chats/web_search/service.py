"""
WebSearchService — thin wrapper around a provider that adds:

  - Configurable timeout (so a slow provider never blocks the main answer)
  - Structured logging at every stage (start / end / error / timeout)
  - Graceful failure: always returns a list (possibly empty) — never raises

Factory
-------
``build_web_search_service()`` reads Django settings and returns a ready-to-use
``WebSearchService``, or ``None`` when web search is disabled or misconfigured.
Call it once at module level (lazy singleton via ``_get_web_search_service``).
"""
from __future__ import annotations

import concurrent.futures
import logging
import time
from typing import Optional

from .base import WebSearchProvider, WebSearchResult

logger = logging.getLogger(__name__)

# Module-level singleton so the provider is constructed only once per process.
_singleton: Optional["WebSearchService"] = None


class WebSearchService:
    """
    Executes web searches with a hard timeout and full exception safety.

    Args:
        provider: A concrete WebSearchProvider implementation.
        timeout_sec: Maximum seconds to wait for the provider. Results are
                     discarded and an empty list is returned on timeout.
        num_results: Number of results to request from the provider.
    """

    def __init__(
        self,
        provider: WebSearchProvider,
        timeout_sec: float = 10.0,
        num_results: int = 5,
    ) -> None:
        self.provider = provider
        self.timeout_sec = timeout_sec
        self.num_results = num_results

    def search(self, query: str) -> list[WebSearchResult]:
        """
        Run a web search and return results.

        This method never raises. On timeout or provider error it logs the
        failure and returns an empty list so the caller can continue with
        RAG-only context.

        Args:
            query: The search query string.

        Returns:
            List of WebSearchResult objects, or [] on failure/timeout.
        """
        t_start = time.monotonic()
        logger.info("Web search starting | query=%s", query[:120])

        # Do NOT use ThreadPoolExecutor as a context manager here.
        # The `with` form calls shutdown(wait=True) on exit, which blocks
        # until the background thread finishes — meaning a timeout on
        # future.result() would still wait for the full search to complete
        # before the except clause runs.  shutdown(wait=False) lets the
        # thread finish on its own without blocking the caller.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(self.provider.search, query, self.num_results)
            results = future.result(timeout=self.timeout_sec)

            elapsed = time.monotonic() - t_start
            logger.info(
                "Web search completed | results=%d | elapsed=%.2fs",
                len(results),
                elapsed,
            )
            return results

        except concurrent.futures.TimeoutError:
            elapsed = time.monotonic() - t_start
            logger.warning(
                "Web search timed out after %.2fs (limit=%.2fs) | query=%s",
                elapsed,
                self.timeout_sec,
                query[:80],
            )
            return []

        except Exception:
            elapsed = time.monotonic() - t_start
            logger.error(
                "Web search failed after %.2fs | query=%s",
                elapsed,
                query[:80],
                exc_info=True,
            )
            return []

        finally:
            executor.shutdown(wait=False)


def build_web_search_service() -> Optional[WebSearchService]:
    """
    Construct a WebSearchService from Django settings.

    Returns ``None`` when:
      - ``WEB_SEARCH_ENABLED`` is False
      - ``WEB_SEARCH_PROVIDER`` is unrecognised
      - Required API keys are missing

    Supported providers (``WEB_SEARCH_PROVIDER`` setting):
      - ``"tavily"`` — requires ``TAVILY_API_KEY``
    """
    from src import settings  # local import to avoid circular import at module load

    if not getattr(settings, "WEB_SEARCH_ENABLED", False):
        logger.debug("Web search is disabled (WEB_SEARCH_ENABLED=False)")
        return None

    provider_name: str = getattr(settings, "WEB_SEARCH_PROVIDER", "tavily").lower()
    timeout_sec: float = float(getattr(settings, "WEB_SEARCH_TIMEOUT_SEC", 10))
    num_results: int = int(getattr(settings, "WEB_SEARCH_NUM_RESULTS", 5))

    provider: Optional[WebSearchProvider] = None

    if provider_name == "tavily":
        api_key: str = getattr(settings, "TAVILY_API_KEY", "")
        if not api_key:
            logger.error(
                "TAVILY_API_KEY is not set — web search will be disabled"
            )
            return None
        try:
            from .tavily_provider import TavilyProvider

            provider = TavilyProvider(api_key=api_key)
            logger.info("Web search provider: Tavily (timeout=%.1fs)", timeout_sec)
        except ImportError:
            logger.error(
                "tavily-python is not installed. "
                "Run: pip install tavily-python"
            )
            return None

    elif provider_name == "duckduckgo":
        try:
            from .duckduckgo_provider import DuckDuckGoProvider

            provider = DuckDuckGoProvider()
            logger.info("Web search provider: DuckDuckGo (timeout=%.1fs)", timeout_sec)
        except ImportError:
            logger.error(
                "duckduckgo-search is not installed. "
                "Run: pip install duckduckgo-search"
            )
            return None

    else:
        logger.error(
            "Unknown web search provider: %r. Supported: 'tavily', 'duckduckgo'",
            provider_name,
        )
        return None

    return WebSearchService(
        provider=provider,
        timeout_sec=timeout_sec,
        num_results=num_results,
    )


def get_web_search_service() -> Optional[WebSearchService]:
    """
    Return the process-level WebSearchService singleton.

    The service is constructed once on first call and reused for all
    subsequent calls. Thread-safe under the GIL for simple assignment.
    """
    global _singleton
    if _singleton is None:
        _singleton = build_web_search_service()
    return _singleton
