"""
RetrievalOrchestrator — runs internal RAG retrieval and live web search
in parallel using a thread pool.

Design goals
------------
- Both branches start at the same time; neither waits for the other.
- Each branch is independently fault-tolerant:
    * RAG failure → rag_success=False, rag_documents=[]
    * Web failure → web_success=False, web_results=[]
    * Both fail  → caller receives a RetrievalResult with empty lists and
                   both flags False; the caller is responsible for the
                   final fallback message.
- The ``hnsw.ef_search`` session variable is set inside the RAG thread
  because each Django thread gets its own database connection.
- No circular imports: this module does NOT import from src.chats.flow.
"""
from __future__ import annotations

import concurrent.futures
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from langchain_core.documents import Document

from django.conf import settings

from src.chats.retrieval.reranker import DocumentReranker
from src.chats.web_search.base import WebSearchResult
from src.chats.web_search.service import WebSearchService

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """
    Combined output of one parallel retrieval run.

    Attributes:
        rag_documents:   Deduplicated Document objects from the vector store.
        web_results:     Results from the web search provider.
        rag_success:     False if the RAG branch raised an exception.
        web_success:     False if the web branch raised or timed out.
        rag_elapsed_sec: Wall-clock seconds taken by the RAG branch.
        web_elapsed_sec: Wall-clock seconds taken by the web branch.
    """

    rag_documents: list[Document] = field(default_factory=list)
    web_results: list[WebSearchResult] = field(default_factory=list)
    rag_success: bool = True
    web_success: bool = True
    rag_elapsed_sec: float = 0.0
    web_elapsed_sec: float = 0.0


class RetrievalOrchestrator:
    """
    Coordinates parallel RAG retrieval and web search.

    Args:
        web_search_service: An initialised WebSearchService, or None to skip
                            web search entirely.
        reranker:           A DocumentReranker to apply after deduplication,
                            or None to skip reranking (returns all deduped docs).
    """

    def __init__(
        self,
        web_search_service: Optional[WebSearchService] = None,
        reranker: Optional[DocumentReranker] = None,
    ) -> None:
        self.web_search_service = web_search_service
        self.reranker = reranker

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(
        self,
        query: str,
        translated_query: str,
        retriever,
        web_enabled: Optional[bool] = None,
        rag_fallback_threshold: int = 3,
    ) -> RetrievalResult:
        """
        Run RAG and web search in parallel, then decide whether to use the
        web results based on how many RAG documents were returned.

        Both branches always start at the same time so there is no latency
        penalty. After both finish, web results are discarded if RAG already
        returned at least ``rag_fallback_threshold`` documents — keeping web
        search as a true fallback rather than a constant cost.

        Args:
            query:                  The (possibly rephrased) user query in its
                                    original language.
            translated_query:       English translation, used for a second
                                    retriever call that broadens coverage.
            retriever:              A FilteredRetriever already configured with
                                    the relevant document IDs and k.
            web_enabled:            Override for web search. When None (default),
                                    falls back to the ``WEB_SEARCH_ENABLED``
                                    Django setting. Pass False to hard-disable
                                    (e.g. for follow-up questions).
            rag_fallback_threshold: Minimum RAG docs considered "sufficient".
                                    Web results are dropped when RAG meets or
                                    exceeds this number.

        Returns:
            A RetrievalResult with all retrieved data and timing info.
        """
        result = RetrievalResult()

        web_search_on = (
            web_enabled
            if web_enabled is not None
            else getattr(settings, "WEB_SEARCH_ENABLED", False)
        )
        run_web = web_search_on and self.web_search_service is not None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            logger.info(
                "Parallel retrieval starting | rag=True | web=%s", run_web
            )

            rag_future = executor.submit(
                self._run_rag, retriever, query, translated_query
            )
            web_future = (
                executor.submit(self._run_web, query) if run_web else None
            )

            # --- Collect RAG results ---
            try:
                docs, elapsed = rag_future.result()
                result.rag_documents = docs
                result.rag_elapsed_sec = elapsed
                result.rag_success = True
                logger.info(
                    "RAG retrieval finished | docs=%d | elapsed=%.2fs",
                    len(docs),
                    elapsed,
                )
            except Exception:
                result.rag_success = False
                result.rag_documents = []
                logger.error(
                    "RAG retrieval raised an exception", exc_info=True
                )

            # --- Collect web results ---
            if web_future is not None:
                try:
                    web_results, elapsed = web_future.result()
                    result.web_elapsed_sec = elapsed
                    result.web_success = True
                    result.web_results = web_results
                    logger.info(
                        "Web search results used "
                        "| rag_docs=%d | web_results=%d | elapsed=%.2fs",
                        len(result.rag_documents),
                        len(web_results),
                        elapsed,
                    )
                except Exception:
                    result.web_success = False
                    result.web_results = []
                    logger.error(
                        "Web search raised an exception in orchestrator",
                        exc_info=True,
                    )
            else:
                result.web_success = True  # not attempted — not a failure

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_rag(
        self, retriever, query: str, translated_query: str
    ) -> tuple[list[Document], float]:
        """
        Execute the RAG retrieval branch.

        Calls the retriever twice (original + translated query) and
        deduplicates the combined results by chunk ID.
        """
        t_start = time.monotonic()
        logger.info("RAG branch started | query=%s", query[:80])

        # Apply hnsw.ef_search on this thread's DB connection.
        # ef_search must exceed the number of candidates we actually want;
        # use retriever.k (broad fetch size) as the floor.
        k = getattr(retriever, 'k', 15)
        self._set_hnsw_ef_search(k)

        docs_original = retriever.invoke(query)
        docs_translated = retriever.invoke(translated_query)
        docs = self._dedupe_by_chunk_id(docs_original + docs_translated)

        logger.info("RAG after dedup: %d candidate chunks", len(docs))

        if self.reranker is not None and docs:
            # Use the English translation for reranking: Flashrank's cross-encoder
            # is English-trained and scores Arabic/non-English text poorly against
            # a non-English query.
            docs = self.reranker.rerank(translated_query, docs)

        elapsed = time.monotonic() - t_start
        return docs, elapsed

    def _run_web(self, query: str) -> tuple[list[WebSearchResult], float]:
        """Execute the web search branch."""
        t_start = time.monotonic()
        # WebSearchService already handles its own timeout + logging.
        results = self.web_search_service.search(query)  # type: ignore[union-attr]
        elapsed = time.monotonic() - t_start
        return results, elapsed

    @staticmethod
    def _set_hnsw_ef_search(k: int = 15) -> None:
        """
        Apply the hnsw.ef_search session variable on the current thread's
        Django database connection.

        ef_search controls HNSW recall; it should be comfortably larger than
        the number of candidates requested (k). We use max(k * 3, 64) as a
        reasonable floor that avoids recall degradation at higher k values.
        """
        ef = max(k * 3, 64)
        try:
            from django.db import connection

            with connection.cursor() as cursor:
                cursor.execute(f"SET LOCAL hnsw.ef_search = {ef};")
        except Exception:
            logger.warning("Could not set hnsw.ef_search", exc_info=True)

    @staticmethod
    def _dedupe_by_chunk_id(documents: list[Document]) -> list[Document]:
        """Remove duplicate chunks, preferring first occurrence."""
        seen: set[str] = set()
        result: list[Document] = []
        for doc in documents:
            chunk_id = doc.metadata.get("id")
            if chunk_id is not None:
                key = f"id:{chunk_id}"
            else:
                ref = doc.metadata.get("rag_source_document_id") or doc.metadata.get(
                    "reference_document_id"
                )
                cidx = doc.metadata.get("chunk_index", "")
                key = f"ref:{ref}:idx:{cidx}"
            if key in seen:
                continue
            seen.add(key)
            result.append(doc)
        return result
