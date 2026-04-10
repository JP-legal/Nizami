import json
import logging
import re
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any, Dict, List, Optional
import unicodedata
from hijri_converter import Hijri

import boto3
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.db import close_old_connections
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.reference_documents.models import RagSourceDocument, RagSourceDocumentChunk
from src.reference_documents.utils import generate_description_for_text
from src.settings import embeddings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120


def _safe_for_log(value: Any) -> str:
    """Return an ASCII-safe string for logs in ASCII-only runtimes."""
    try:
        text = str(value)
    except Exception:
        text = repr(value)
    return text.encode("ascii", "backslashreplace").decode("ascii")


def _debug_print(*, message: str) -> None:
    payload = (message + "\n").encode("ascii", "backslashreplace")
    sys.stdout.buffer.write(payload)
    sys.stdout.flush()


def _non_ascii_probe(*, label: str, text: str, sample_size: int = 8) -> str:
    issues = []
    for idx, ch in enumerate(text):
        if ord(ch) > 127:
            issues.append(f"{idx}:{hex(ord(ch))}:{unicodedata.name(ch, 'UNKNOWN')}")
            if len(issues) >= sample_size:
                break
    return f"{label} len={len(text)} non_ascii_samples={issues if issues else 'none'}"


_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",   # '  LEFT SINGLE QUOTATION MARK
        "\u2019": "'",   # '  RIGHT SINGLE QUOTATION MARK
        "\u201a": "'",   # ‚  SINGLE LOW-9 QUOTATION MARK
        "\u201b": "'",   # ‛  SINGLE HIGH-REVERSED-9 QUOTATION MARK
        "\u201c": '"',   # "  LEFT DOUBLE QUOTATION MARK
        "\u201d": '"',   # "  RIGHT DOUBLE QUOTATION MARK
        "\u201e": '"',   # „  DOUBLE LOW-9 QUOTATION MARK
        "\u201f": '"',   # ‟  DOUBLE HIGH-REVERSED-9 QUOTATION MARK
        "\u00ab": '"',   # «  LEFT-POINTING DOUBLE ANGLE QUOTATION MARK
        "\u00bb": '"',   # »  RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK
        "\u2039": "'",   # ‹  SINGLE LEFT-POINTING ANGLE QUOTATION MARK
        "\u203a": "'",   # ›  SINGLE RIGHT-POINTING ANGLE QUOTATION MARK
        "\u2032": "'",   # ′  PRIME
        "\u2033": '"',   # ″  DOUBLE PRIME
        "\u2014": "-",   # —  EM DASH
        "\u2013": "-",   # –  EN DASH
        "\u2026": "...", # …  HORIZONTAL ELLIPSIS
        "\u00a0": " ",   # NBSP
    }
)


# Regex that matches every fancy/curly quote variant that causes ASCII-encoding
# failures in the embedding API, regardless of whether translate() caught them.
_FANCY_SINGLE_QUOTES_RE = re.compile(
    r"[\u2018\u2019\u201a\u201b\u2039\u203a\u2032]"
)
_FANCY_DOUBLE_QUOTES_RE = re.compile(
    r"[\u201c\u201d\u201e\u201f\u00ab\u00bb\u2033]"
)


def _clean_text_for_embedding(*, text: str) -> str:
    """Normalize text while preserving Arabic content before embedding."""
    cleaned = unicodedata.normalize("NFC", text)
    cleaned = cleaned.translate(_QUOTE_MAP)
    # Remove invisible/control chars except newlines/tabs.
    cleaned = "".join(ch for ch in cleaned if ch in "\n\r\t" or unicodedata.category(ch)[0] != "C")
    # Belt-and-suspenders: regex pass guarantees no fancy quote survives even
    # if translate() or the Pi/Pf loop missed one (e.g. in non-standard locales).
    cleaned = _FANCY_SINGLE_QUOTES_RE.sub("'", cleaned)
    cleaned = _FANCY_DOUBLE_QUOTES_RE.sub('"', cleaned)
    return cleaned


def _sanitize_payload_strings(value: Any) -> Any:
    """Recursively replace smart quotes in incoming payload strings."""
    if isinstance(value, str):
        return value.translate(_QUOTE_MAP)
    if isinstance(value, list):
        return [_sanitize_payload_strings(item) for item in value]
    if isinstance(value, dict):
        return {k: _sanitize_payload_strings(v) for k, v in value.items()}
    return value


class Command(BaseCommand):
    help = (
        "Fetch clean_text from S3 for RagSourceDocuments, chunk it, generate embeddings, "
        "generate a description + description embedding, and store everything in "
        "RagSourceDocumentChunk. Mirrors the ReferenceDocument pipeline but for S3 RAG docs."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--bucket",
            type=str,
            default=getattr(settings, "RAG_S3_BUCKET", ""),
            help="S3 bucket override (defaults to RAG_S3_BUCKET).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Number of chunks to embed in a single OpenAI call (default 50).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-embed documents that are already marked is_embedded=True.",
        )
        parser.add_argument(
            "--created-after",
            type=str,
            help="Only process documents with created_at date strictly after this date (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--created-on",
            type=str,
            help="Only process documents with created_at on this date (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--offset",
            type=int,
            default=0,
            help="Skip the first N documents after filtering (for partitioned execution).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of documents to process after applying offset.",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=4,
            help="Number of worker threads to process documents.",
        )
        parser.add_argument(
            "--document-id",
            type=int,
            action="append",
            dest="document_ids",
            default=None,
            help=(
                "Only process RagSourceDocument row(s) with this primary key. "
                "Repeat the flag for multiple ids. Combine with --force to re-embed already embedded docs."
            ),
        )

    def handle(self, *args, **options):
        bucket: Optional[str] = options.get("bucket")
        batch_size: int = options["batch_size"]
        force: bool = options["force"]
        created_after_str: Optional[str] = options.get("created_after")
        created_on_str: Optional[str] = options.get("created_on")
        offset: int = options["offset"]
        limit: Optional[int] = options.get("limit")
        workers: int = options["workers"]
        document_ids: Optional[List[int]] = options.get("document_ids")

        if not bucket:
            logger.error("Bucket name is required (use --bucket or RAG_S3_BUCKET env var).")
            return

        if embeddings is None:
            logger.error("OpenAI embeddings not initialised (check OPENAI_API_KEY).")
            return

        qs = RagSourceDocument.objects.all()

        if document_ids:
            qs = qs.filter(id__in=document_ids)

        # Date filtering
        if created_on_str and created_after_str:
            logger.error("Use only one of --created-on or --created-after, not both.")
            return

        if created_on_str:
            try:
                created_on: date = datetime.strptime(created_on_str, "%Y-%m-%d").date()
            except ValueError:
                logger.error("Invalid --created-on date format. Use YYYY-MM-DD.")
                return
            qs = qs.filter(created_at__date=created_on)
        elif created_after_str:
            try:
                created_after: date = datetime.strptime(created_after_str, "%Y-%m-%d").date()
            except ValueError:
                logger.error("Invalid --created-after date format. Use YYYY-MM-DD.")
                return
            qs = qs.filter(created_at__date__gt=created_after)

        if not force:
            qs = qs.filter(is_embedded=False)

        # Partitioning: apply deterministic ordering, then offset/limit
        qs = qs.order_by("id")
        if offset:
            qs = qs[offset:]
        if limit is not None:
            qs = qs[:limit]

        total = qs.count()
        if total == 0:
            logger.info("No documents to embed.")
            return

        logger.info("Starting embedding for %s RagSourceDocument(s) with %s workers…", total, workers)

        # Collect ids so we don't share ORM instances across threads.
        doc_ids = list(qs.values_list("id", flat=True))

        created_total = 0
        failed_total = 0

        def worker(doc_id: int) -> bool:
            logger.info("Starting worker for doc_id=%s", doc_id)
            print(f"[embed-debug] worker_start doc_id={doc_id}")
            close_old_connections()
            try:
                doc = RagSourceDocument.objects.get(id=doc_id)
                print(f"[embed-debug] doc_loaded doc_id={doc_id} s3_bucket={doc.s3_bucket} s3_key={doc.s3_key}")
                s3_client = boto3.client("s3")
                text_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=CHUNK_SIZE,
                    chunk_overlap=CHUNK_OVERLAP,
                )
                self._process_document(doc, s3_client, bucket, text_splitter, batch_size)
                logger.info("Finished embedding doc_id=%s", doc_id)
                return True
            except Exception as exc:
                print(f"[embed-debug] worker_error doc_id={doc_id} type={exc.__class__.__name__} detail={_safe_for_log(exc)}")
                logger.error(
                    "Failed to embed doc id=%s: %s (%s)",
                    doc_id,
                    exc.__class__.__name__,
                    _safe_for_log(exc),
                )
                return False

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_id = {executor.submit(worker, doc_id): doc_id for doc_id in doc_ids}
            for future in as_completed(future_to_id):
                ok = future.result()
                if ok:
                    created_total += 1
                else:
                    failed_total += 1

        logger.info(
            "Done. embedded=%s, failed=%s, total=%s.",
            created_total, failed_total, total,
        )

    # ------------------------------------------------------------------
    def _process_document(
        self,
        doc: RagSourceDocument,
        s3_client,
        bucket: str,
        text_splitter: RecursiveCharacterTextSplitter,
        batch_size: int,
    ):
        s3_bucket = doc.s3_bucket or bucket
        s3_key = doc.s3_key

        if not s3_key:
            raise ValueError("s3_key is empty")

        body = s3_client.get_object(Bucket=s3_bucket, Key=s3_key)["Body"].read()
        _debug_print(message=f"[embed-debug] s3_read_ok doc_id={doc.id} bytes={len(body)}")
        payload = json.loads(body)
        payload = _sanitize_payload_strings(payload)
        _debug_print(message=f"[embed-debug] json_load_ok doc_id={doc.id} keys={list(payload.keys())[:10]}")

        clean_text = payload.get("clean_text")
        if not isinstance(clean_text, str) or not clean_text.strip():
            raise ValueError("missing or empty clean_text")
        _debug_print(message=f"[embed-debug] {_non_ascii_probe(label='clean_text_raw', text=clean_text)}")
        clean_text = _clean_text_for_embedding(text=clean_text)
        _debug_print(message=f"[embed-debug] {_non_ascii_probe(label='clean_text_cleaned', text=clean_text)}")

        # ---- Extract and persist metadata columns ----
        _apply_metadata(doc, payload)
        _debug_print(message=f"[embed-debug] metadata_applied doc_id={doc.id}")

        # ---- Delete old chunks if force-re-embedding ----
        RagSourceDocumentChunk.objects.filter(rag_source_document=doc).delete()

        # ---- Chunk ----
        chunks: List[str] = text_splitter.split_text(clean_text)
        chunks = [_clean_text_for_embedding(text=chunk) for chunk in chunks]
        if not chunks:
            raise ValueError("text_splitter produced zero chunks")
        _debug_print(message=f"[embed-debug] chunking_ok doc_id={doc.id} chunk_count={len(chunks)}")
        _debug_print(message=f"[embed-debug] {_non_ascii_probe(label='chunk0', text=chunks[0])}")

        # ---- Embed chunks in batches ----
        all_embeddings: List[List[float]] = []
        for i in range(0, len(chunks), batch_size):
            # Final safety pass: re-apply cleaning so no fancy Unicode survives
            # to the embedding API even if an upstream step missed it.
            batch = [_clean_text_for_embedding(text=chunk) for chunk in chunks[i : i + batch_size]]
            _debug_print(message=f"[embed-debug] embedding_batch_start doc_id={doc.id} start={i} size={len(batch)}")
            batch_embs = embeddings.embed_documents(batch)
            all_embeddings.extend(batch_embs)
            _debug_print(message=f"[embed-debug] embedding_batch_ok doc_id={doc.id} start={i} produced={len(batch_embs)}")

        # ---- Bulk-create chunk rows ----
        chunk_objects = [
            RagSourceDocumentChunk(
                id=uuid.uuid4(),
                rag_source_document=doc,
                content=text,
                embedding=emb,
                chunk_index=idx,
            )
            for idx, (text, emb) in enumerate(zip(chunks, all_embeddings))
        ]
        RagSourceDocumentChunk.objects.bulk_create(chunk_objects, batch_size=100)

        # ---- Generate description & embed it ----
        # Prepend structured metadata so the description embedding encodes
        # document identity (type, entity, date) not just content.
        if not doc.description:
            description_input = _build_description_input(payload, clean_text)
            description_input = _clean_text_for_embedding(text=description_input)
            _debug_print(message=f"[embed-debug] {_non_ascii_probe(label='description_input', text=description_input)}")
            doc.description = generate_description_for_text(description_input, "ar")
            _debug_print(message=f"[embed-debug] description_generated doc_id={doc.id}")
        doc.description = _clean_text_for_embedding(text=doc.description)
        _debug_print(message=f"[embed-debug] {_non_ascii_probe(label='description_cleaned', text=doc.description)}")

        doc.description_embedding = embeddings.embed_query(doc.description)
        _debug_print(message=f"[embed-debug] description_embedding_ok doc_id={doc.id}")
        doc.is_embedded = True
        doc.save(update_fields=[
            "description", "description_embedding", "is_embedded",
            "doc_type", "entity", "date_gregorian", "date_hijri",
            "decision_number", "decision_date_hijri", "source",
            "incomplete_flag", "is_duplicate", "format_confidence",
            "updated_at",
        ])

        logger.info(
            "Embedded doc id=%s  chunks=%s",
            doc.id, len(chunk_objects),
        )


_AR_NUMBERS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

_AR_HIJRI_MONTHS = {
    "محرم": 1, "صفر": 2, "ربيع الاول": 3, "ربيع الثاني": 4,
    "جمادي الاول": 5, "جمادي الثانية": 6, "رجب": 7, "شعبان": 8,
    "رمضان": 9, "شوال": 10, "ذو القعدة": 11, "ذو الحجة": 12,
}


def _parse_arabic_hijri(text: str) -> Optional[str]:
    """Parse an Arabic hijri date string like '٣٠ جمادي الاول ١٤٤٤' → 'day/month/year'."""
    try:
        text = re.sub(r"\s+", " ", text.translate(_AR_NUMBERS).strip())
        match = re.match(r"(\d+)\s+(.+)\s+(\d+)", text)
        if not match:
            return None
        day, month_name, year = match.groups()
        month = _AR_HIJRI_MONTHS.get(month_name.strip())
        if not month:
            return None
        return f"{day}/{month}/{year}"
    except Exception:
        return None


def _hijri_str_to_gregorian(hijri_str: str) -> Optional[date]:
    """Convert 'day/month/year' hijri string to a Gregorian date."""
    try:
        parts = hijri_str.split("/")
        if len(parts) != 3:
            return None
        day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
        return Hijri(year, month, day).to_gregorian().date()
    except Exception:
        return None


def _apply_metadata(doc: "RagSourceDocument", payload: Dict[str, Any]) -> None:
    """Write structured metadata fields from the S3 JSON payload onto the model instance."""
    meta: Dict[str, Any] = payload.get("metadata") or payload

    doc.doc_type = meta.get("doc_type") or None
    doc.entity = meta.get("entity") or None
    doc.date_hijri = meta.get("date_hijri_dual") or None
    doc.decision_number = meta.get("decision_number") or None
    doc.decision_date_hijri = meta.get("decision_date_hijri") or None
    doc.source = payload.get("source") or None
    doc.incomplete_flag = bool(meta.get("incomplete_flag", False))
    doc.is_duplicate = bool(meta.get("is_duplicate_filename", False))

    raw_confidence = (meta.get("format_detection") or {}).get("confidence")
    doc.format_confidence = float(raw_confidence) if raw_confidence is not None else None

    raw_date = meta.get("date_gregorian")
    if raw_date:
        try:
            doc.date_gregorian = date.fromisoformat(raw_date)
        except (ValueError, TypeError):
            doc.date_gregorian = None
    else:
        doc.date_gregorian = None

    # If date_hijri_dual was missing, try to derive both dates from date_hijri
    if not doc.date_hijri:
        raw_hijri = meta.get("date_hijri")
        if raw_hijri:
            parsed = _parse_arabic_hijri(raw_hijri)
            if parsed:
                doc.date_hijri = parsed
                if not doc.date_gregorian:
                    doc.date_gregorian = _hijri_str_to_gregorian(parsed)


def _build_description_input(payload: Dict[str, Any], clean_text: str) -> str:
    """
    Prepend a structured metadata header to clean_text so the LLM-generated
    description — and its embedding — encodes document identity (type, entity,
    date, decision number) alongside content semantics.
    """
    meta: Dict[str, Any] = payload.get("metadata") or payload

    parts = []
    if meta.get("doc_type"):
        parts.append(f"نوع الوثيقة: {meta['doc_type']}")
    if meta.get("entity"):
        parts.append(f"الجهة: {meta['entity']}")
    if meta.get("date_gregorian"):
        parts.append(f"التاريخ: {meta['date_gregorian']}")
    if meta.get("decision_number"):
        parts.append(f"رقم القرار: {meta['decision_number']}")

    if not parts:
        return clean_text

    header = " | ".join(parts)
    return f"[{header}]\n{clean_text}"