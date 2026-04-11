"""
Vector similarity search retrievers with document filtering.

This module provides utilities for performing similarity searches with document ID filtering,
which is necessary because PGVector's filter syntax doesn't support filter-before-search.

Supports two backends controlled by the RAG_SOURCE setting:
  - "old": searches the langchain_pg_embedding table (ReferenceDocument pipeline)
  - "new": searches the reference_documents_ragsourcedocumentchunk table (S3 RAG pipeline)
"""
import json
import logging

from django.db import connection
from langchain_core.documents import Document


def similarity_search_with_document_filter(query_text, document_ids, k=8, embeddings=None, logger=None):
    """
    Perform similarity search filtered by document IDs using raw SQL.
    
    This is necessary because PGVector's filter syntax doesn't support 
    filter-before-search, which can result in zero results when target 
    documents don't appear in the top global results.
    
    Args:
        query_text: Text to search for
        document_ids: List of document IDs to filter by
        k: Number of results to return
        embeddings: Embeddings model (defaults to settings.embeddings)
        logger: Optional logger instance
    
    Returns:
        List of Document objects or None if search fails
    """
    if embeddings is None:
        from src.settings import embeddings
    
    if not document_ids:
        return []
    
    try:
        query_emb = embeddings.embed_query(query_text)
        
        with connection.cursor() as cursor:
            # Format embedding as vector string for pgvector
            embedding_str = '[' + ','.join(str(x) for x in query_emb) + ']'
            document_ids_list = list(document_ids)
            
            # Parameterized query: Filter FIRST, then search within filtered set
            cursor.execute("""
                SELECT 
                    id,
                    document,
                    cmetadata,
                    1 - (embedding <=> %s::vector) as similarity
                FROM langchain_pg_embedding 
                WHERE (cmetadata->>'reference_document_id')::bigint = ANY(%s::bigint[])
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, [embedding_str, document_ids_list, embedding_str, k])
            
            rows = cursor.fetchall()
            if rows:
                docs = []
                for row in rows:
                    chunk_id, document, metadata, similarity = row
                    if isinstance(metadata, str):
                        metadata = json.loads(metadata)
                    elif metadata is None:
                        metadata = {}
                    
                    metadata['id'] = str(chunk_id) if chunk_id else None
                    docs.append(Document(
                        page_content=document or '',
                        metadata=metadata
                    ))
                
                if logger:
                    logger.info(f'Similarity search found {len(docs)} chunks from {len(document_ids)} target documents')
                return docs
    except Exception as e:
        if logger:
            logger.warning(f'Similarity search with document filter failed: {e}')
        return None
    
    return None


def fallback_similarity_search_with_filter(query_text, retriever, document_ids, k=8, logger=None):
    """
    Fallback strategy: Search globally and filter by document ID.
    
    This is less reliable than SQL-based search because it searches globally
    first, then filters. If target documents don't appear in the top global
    results, this will return zero results even if chunks exist.
    
    Args:
        query_text: Text to search for
        retriever: LangChain retriever instance (should search with larger k)
        document_ids: Set of document IDs to filter by
        k: Number of results to return
        logger: Optional logger instance
    
    Returns:
        List of Document objects filtered by document_ids
    """
    if logger:
        logger.info('Using FALLBACK strategy: searching globally then filtering')
    
    all_docs = retriever.invoke(query_text)
    
    # Filter by reference_document_id in metadata
    filtered_docs = []
    for doc in all_docs:
        ref_id = doc.metadata.get('reference_document_id')
        if ref_id is not None:
            try:
                ref_id_int = int(ref_id) if isinstance(ref_id, str) else ref_id
                if ref_id_int in document_ids:
                    filtered_docs.append(doc)
                    if len(filtered_docs) >= k:
                        break
            except (ValueError, TypeError):
                pass
    
    if len(filtered_docs) == 0:
        found_doc_ids = {int(doc.metadata.get('reference_document_id')) 
                        for doc in all_docs 
                        if doc.metadata.get('reference_document_id')}
        if logger:
            logger.warning(f'Fallback: Base search returned chunks from {found_doc_ids}, but looking for {document_ids}')
    else:
        if logger:
            logger.info(f'Fallback strategy found {len(filtered_docs)} chunks')
    
    return filtered_docs[:k]


def rag_source_similarity_search(query_text, document_ids, k=8, embeddings=None, logger=None):
    """
    Similarity search against RagSourceDocumentChunk (the new S3 RAG table).

    Same pattern as similarity_search_with_document_filter but queries the
    Django-managed reference_documents_ragsourcedocumentchunk table.
    """
    if embeddings is None:
        from src.settings import embeddings

    if not document_ids:
        return []

    try:
        query_emb = embeddings.embed_query(query_text)

        with connection.cursor() as cursor:
            embedding_str = '[' + ','.join(str(x) for x in query_emb) + ']'
            document_ids_list = list(document_ids)

            cursor.execute("""
                SELECT
                    c.id,
                    c.content,
                    c.rag_source_document_id,
                    c.chunk_index,
                    d.title,
                    1 - (c.embedding <=> %s::vector) AS similarity
                FROM reference_documents_ragsourcedocumentchunk c
                JOIN reference_documents_ragsourcedocument d ON d.id = c.rag_source_document_id
                WHERE c.rag_source_document_id = ANY(%s::bigint[])
                ORDER BY c.embedding <=> %s::vector
                LIMIT %s
            """, [embedding_str, document_ids_list, embedding_str, k])

            rows = cursor.fetchall()
            if rows:
                docs = []
                for row in rows:
                    chunk_id, content, doc_id, chunk_idx, title, similarity = row
                    metadata = {
                        'id': str(chunk_id),
                        'rag_source_document_id': doc_id,
                        'chunk_index': chunk_idx,
                        'title': title or '',
                        'language': 'ar',
                    }
                    docs.append(Document(page_content=content or '', metadata=metadata))

                if logger:
                    logger.info(
                        'rag_source_similarity_search found %s chunks from %s target documents',
                        len(docs), len(document_ids),
                    )
                return docs
    except Exception as e:
        if logger:
            logger.warning('rag_source_similarity_search failed: %s', e)
        return None

    return None


def find_rag_source_document_ids_by_description(text):
    """
    Find the most relevant RagSourceDocument IDs by description embedding similarity.
    Mirrors find_ref_document_ids_by_description but for the new table.
    """
    from pgvector.django import CosineDistance
    from src.reference_documents.models import RagSourceDocument
    from src.settings import embeddings

    embedded_text = embeddings.embed_query(text)

    files = (
        RagSourceDocument.objects
        .filter(is_embedded=True)
        .order_by(CosineDistance('description_embedding', embedded_text))
        .values('id')[:10]
    )
    return list(f['id'] for f in files)


def _keyword_search_chunks(query_text, document_ids, k, logger=None):
    """
    Full-text keyword search against RagSourceDocumentChunk using PostgreSQL tsvector.

    Complements vector search by exact-matching key legal terms (law names, article
    numbers, penalty amounts) that may rank low on cosine similarity alone.

    Tries the 'arabic' text-search configuration first; falls back to 'simple' if
    the arabic config is not installed on the database.

    Returns a list of Document objects ordered by ts_rank DESC, or [] on failure.
    """
    if not document_ids or not query_text:
        return []

    document_ids_list = list(document_ids)

    for config in ('arabic', 'simple'):
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        c.id,
                        c.content,
                        c.rag_source_document_id,
                        c.chunk_index,
                        d.title,
                        ts_rank(
                            to_tsvector(%s, c.content),
                            plainto_tsquery(%s, %s)
                        ) AS rank
                    FROM reference_documents_ragsourcedocumentchunk c
                    JOIN reference_documents_ragsourcedocument d
                        ON d.id = c.rag_source_document_id
                    WHERE c.rag_source_document_id = ANY(%s::bigint[])
                      AND to_tsvector(%s, c.content) @@ plainto_tsquery(%s, %s)
                    ORDER BY rank DESC
                    LIMIT %s
                    """,
                    [config, config, query_text,
                     document_ids_list,
                     config, config, query_text,
                     k],
                )
                rows = cursor.fetchall()

            docs = []
            for chunk_id, content, doc_id, chunk_idx, title, _rank in rows:
                docs.append(Document(
                    page_content=content or '',
                    metadata={
                        'id': str(chunk_id),
                        'rag_source_document_id': doc_id,
                        'chunk_index': chunk_idx,
                        'title': title or '',
                        'language': 'ar',
                    },
                ))

            if logger:
                logger.info(
                    'keyword_search_chunks[%s] found %s chunks for query "%s..."',
                    config, len(docs), query_text[:40],
                )
            return docs

        except Exception as exc:
            if logger:
                logger.warning(
                    'keyword_search_chunks[%s] failed: %s — %s',
                    config, type(exc).__name__, exc,
                )
            # If the 'arabic' config caused the error, try 'simple'; otherwise give up.
            if config == 'simple':
                return []

    return []


def _expand_with_neighbors(docs, logger=None):
    """
    For each retrieved chunk, also fetch its immediate neighbors (chunk_index ± 1)
    from the same rag_source_document_id.

    This ensures the LLM receives both an article's title/header chunk and its
    penalty/body chunk when they fall in adjacent 800-char windows.

    Deduplicates by chunk UUID. The returned list is capped at min(len(docs)*3, 30)
    to prevent context bloat.
    """
    if not docs:
        return docs

    # Build lookup of (doc_id, index) pairs we need to fetch
    pairs = set()
    for doc in docs:
        doc_id = doc.metadata.get('rag_source_document_id')
        idx = doc.metadata.get('chunk_index')
        if doc_id is not None and idx is not None:
            pairs.add((doc_id, idx - 1))
            pairs.add((doc_id, idx + 1))

    if not pairs:
        return docs

    # Remove pairs we already have
    existing_ids = {doc.metadata.get('id') for doc in docs}
    existing_pairs = {
        (doc.metadata.get('rag_source_document_id'), doc.metadata.get('chunk_index'))
        for doc in docs
    }
    pairs -= existing_pairs

    if not pairs:
        return docs

    # Group pairs by doc_id for an efficient single query
    from collections import defaultdict
    pairs_by_doc: dict = defaultdict(list)
    for doc_id, idx in pairs:
        if idx >= 0:  # chunk_index is always non-negative
            pairs_by_doc[doc_id].append(idx)

    if not pairs_by_doc:
        return docs

    try:
        neighbor_docs = []
        with connection.cursor() as cursor:
            for doc_id, indexes in pairs_by_doc.items():
                cursor.execute(
                    """
                    SELECT c.id, c.content, c.rag_source_document_id, c.chunk_index, d.title
                    FROM reference_documents_ragsourcedocumentchunk c
                    JOIN reference_documents_ragsourcedocument d ON d.id = c.rag_source_document_id
                    WHERE c.rag_source_document_id = %s
                      AND c.chunk_index = ANY(%s::int[])
                    """,
                    [doc_id, indexes],
                )
                for chunk_id, content, rdoc_id, chunk_idx, title in cursor.fetchall():
                    chunk_uuid = str(chunk_id)
                    if chunk_uuid not in existing_ids:
                        existing_ids.add(chunk_uuid)
                        neighbor_docs.append(Document(
                            page_content=content or '',
                            metadata={
                                'id': chunk_uuid,
                                'rag_source_document_id': rdoc_id,
                                'chunk_index': chunk_idx,
                                'title': title or '',
                                'language': 'ar',
                            },
                        ))

        cap = min(len(docs) * 3, 30)
        combined = (docs + neighbor_docs)[:cap]

        if logger and neighbor_docs:
            logger.info(
                'expand_with_neighbors added %s neighbor chunks (total %s, cap %s)',
                len(neighbor_docs), len(combined), cap,
            )
        return combined

    except Exception as exc:
        if logger:
            logger.warning('expand_with_neighbors failed: %s', exc)
        return docs


class FilteredRetriever:
    """
    Custom retriever that filters similarity search by document IDs.

    Retrieval strategy for RAG_SOURCE="new":
    1. Vector search: cosine-distance ranking via pgvector (primary)
    2. Keyword search: PostgreSQL tsvector full-text search (catches exact legal terms)
    3. Neighbor expansion: fetches chunk_index ± 1 for each result to surface
       adjacent article-title / penalty chunks that may be in separate windows
    4. The merged candidate list is passed to the Flashrank reranker

    For RAG_SOURCE="old":
    1. SQL-based search: Filters FIRST, then searches within filtered set (preferred)
    2. Fallback search: Searches globally, then filters (less reliable)
    """

    def __init__(self, document_ids, k=8, logger=None, vectorstore=None):
        from src.settings import vectorstore as default_vectorstore, RAG_SOURCE

        self.document_ids = set(document_ids) if document_ids else set()
        self.k = k
        self.logger = logger or logging.getLogger(__name__)
        self.rag_source = RAG_SOURCE
        self.vectorstore = vectorstore or default_vectorstore
        if self.rag_source != 'new' and self.vectorstore is not None:
            self.base_retriever = self.vectorstore.as_retriever(
                search_kwargs={'k': max(k * 20, 100)},
            )
        else:
            self.base_retriever = None

    def _sql_based_search(self, query_text):
        return similarity_search_with_document_filter(
            query_text=query_text,
            document_ids=self.document_ids,
            k=self.k,
            logger=self.logger,
        )

    def _rag_source_search(self, query_text):
        # 1. Vector search
        vector_docs = rag_source_similarity_search(
            query_text=query_text,
            document_ids=self.document_ids,
            k=self.k,
            logger=self.logger,
        ) or []

        # 2. Keyword search — half the vector k to avoid drowning vector results
        keyword_docs = _keyword_search_chunks(
            query_text=query_text,
            document_ids=self.document_ids,
            k=max(self.k // 2, 5),
            logger=self.logger,
        )

        # 3. Merge by chunk ID (vector results have priority; keyword fills gaps)
        seen_ids = {doc.metadata['id'] for doc in vector_docs}
        for doc in keyword_docs:
            if doc.metadata['id'] not in seen_ids:
                seen_ids.add(doc.metadata['id'])
                vector_docs.append(doc)

        # 4. Expand with adjacent chunks so article headers + bodies are both present
        expanded = _expand_with_neighbors(vector_docs, logger=self.logger)

        return expanded if expanded else None

    def _fallback_search(self, query_text):
        if self.base_retriever is None:
            return []
        return fallback_similarity_search_with_filter(
            query_text=query_text,
            retriever=self.base_retriever,
            document_ids=self.document_ids,
            k=self.k,
            logger=self.logger,
        )

    def invoke(self, query_text):
        if not self.document_ids:
            return []

        if self.rag_source == 'new':
            docs = self._rag_source_search(query_text)
            return docs if docs is not None else []

        docs = self._sql_based_search(query_text)
        if docs is not None:
            return docs

        return self._fallback_search(query_text)

