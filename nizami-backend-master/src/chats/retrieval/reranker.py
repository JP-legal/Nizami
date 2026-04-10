"""
Cross-encoder reranker for RAG retrieved documents.

Uses Flashrank (no API key, lightweight) via the LangChain community wrapper.
Falls back gracefully to original ordering if the library is unavailable.
"""
from __future__ import annotations

import logging
from typing import Dict
from langchain_core.documents import Document

logger = logging.getLogger(__name__)

# Cache instances by top_n so the Ranker model is loaded once per process,
# not once per request. Model loading (Ranker.__init__) downloads and
# initialises a cross-encoder transformer — it must never run per-request.
_cache: Dict[int, "DocumentReranker"] = {}


def get_reranker(top_n: int = 6) -> "DocumentReranker":
    """Return a cached DocumentReranker for the given top_n."""
    if top_n not in _cache:
        _cache[top_n] = DocumentReranker(top_n=top_n)
    return _cache[top_n]


class DocumentReranker:
    """
    Thin wrapper around a cross-encoder reranker.

    Each instance is initialised with a fixed ``top_n`` so that the underlying
    backend object is never mutated after construction — making concurrent use
    safe across threads.

    Usage::

        reranker = DocumentReranker(top_n=6)
        ranked = reranker.rerank(query, broad_docs)
    """

    def __init__(self, top_n: int = 6) -> None:
        self.top_n = top_n
        # Build a dedicated backend instance with top_n baked in.
        # Falls back to None if flashrank is unavailable.
        self._backend = self._build_backend(top_n)

    @staticmethod
    def _build_backend(top_n: int):
        try:
            from flashrank import Ranker  # noqa: PLC0415
            from langchain_community.document_compressors.flashrank_rerank import FlashrankRerank  # noqa: PLC0415
            # score_threshold=-1.0 ensures we always get up to top_n results.
            # The default 0.0 silently drops docs whose cross-encoder score is
            # below zero, so we could ask for 6 and receive 2 with no warning.
            # We trust vector retrieval for relevance filtering; the reranker's
            # job here is ordering only.
            #
            # Pass a pre-initialized Ranker so Flashrank reads from the model
            # baked into the image (/app/models) instead of downloading to /tmp
            # at runtime — which races when multiple workers start concurrently.
            ranker = Ranker(model_name="ms-marco-MultiBERT-L-12", cache_dir="/app/models")
            instance = FlashrankRerank(client=ranker, top_n=top_n, score_threshold=-1.0)
            logger.info("Flashrank reranker initialised with top_n=%d", top_n)
            return instance
        except Exception:
            logger.warning("Flashrank unavailable — reranking will be skipped", exc_info=True)
            return None

    def rerank(self, query: str, documents: list[Document]) -> list[Document]:
        """
        Rerank *documents* by relevance to *query* and return the top ``self.top_n``.

        If the reranker backend is unavailable the original list is returned,
        truncated to ``self.top_n``.
        """
        if not documents:
            return documents

        if self._backend is None:
            logger.debug("No reranker backend — truncating to top %d by vector score", self.top_n)
            return documents[: self.top_n]

        try:
            reranked = self._backend.compress_documents(documents, query)
            logger.info("Reranker: %d → %d docs", len(documents), len(reranked))
            return reranked
        except Exception:
            logger.warning(
                "Reranking failed — truncating to top %d by vector score",
                self.top_n,
                exc_info=True,
            )
            return documents[: self.top_n]
