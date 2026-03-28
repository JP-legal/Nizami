import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import boto3
from django.core.management.base import BaseCommand, CommandParser
from django.db import close_old_connections

from src.reference_documents.management.commands.embed_rag_source_documents import _apply_metadata
from src.reference_documents.models import RagSourceDocument

logger = logging.getLogger(__name__)

METADATA_FIELDS = [
    "doc_type", "entity", "date_gregorian", "date_hijri",
    "decision_number", "decision_date_hijri", "source",
    "incomplete_flag", "is_duplicate", "format_confidence",
    "updated_at",
]


class Command(BaseCommand):
    help = (
        "Backfill structured metadata columns on existing RagSourceDocument rows. "
        "Reads s3_bucket and s3_key already stored on each row (set by sync_docs_from_bucket). "
        "Does NOT re-embed — embeddings are untouched."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-populate metadata even for rows that already have doc_type set.",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=4,
            help="Number of worker threads (default 4).",
        )
        parser.add_argument(
            "--offset",
            type=int,
            default=0,
            help="Skip the first N documents after filtering.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of documents to process.",
        )
        parser.add_argument(
            "--document-id",
            type=int,
            action="append",
            dest="document_ids",
            default=None,
            help="Only process specific RagSourceDocument primary key(s). Repeatable.",
        )

    def handle(self, *args, **options):
        force: bool = options["force"]
        workers: int = options["workers"]
        offset: int = options["offset"]
        limit: Optional[int] = options.get("limit")
        document_ids: Optional[List[int]] = options.get("document_ids")

        qs = RagSourceDocument.objects.exclude(s3_key="").exclude(s3_key__isnull=True)

        if document_ids:
            qs = qs.filter(id__in=document_ids)

        if not force:
            qs = qs.filter(doc_type__isnull=True)

        qs = qs.order_by("id")
        if offset:
            qs = qs[offset:]
        if limit is not None:
            qs = qs[:limit]

        total = qs.count()
        if total == 0:
            logger.info("No documents to backfill.")
            return

        logger.info("Backfilling metadata for %s RagSourceDocument(s) with %s workers…", total, workers)
        doc_ids = list(qs.values_list("id", flat=True))

        success_total = 0
        failed_total = 0

        def worker(doc_id: int) -> bool:
            close_old_connections()
            try:
                doc = RagSourceDocument.objects.get(id=doc_id)

                if not doc.s3_bucket or not doc.s3_key:
                    raise ValueError(f"doc id={doc_id} is missing s3_bucket or s3_key")

                s3_client = boto3.client("s3")
                body = s3_client.get_object(Bucket=doc.s3_bucket, Key=doc.s3_key)["Body"].read()
                payload = json.loads(body)

                _apply_metadata(doc, payload)
                doc.save(update_fields=METADATA_FIELDS)

                logger.info(
                    "Backfilled doc id=%s  doc_type=%r  entity=%r  date=%s",
                    doc.id, doc.doc_type, doc.entity, doc.date_gregorian,
                )
                return True
            except Exception as exc:
                logger.error("Failed to backfill doc id=%s: %s", doc_id, exc)
                return False

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(worker, doc_id): doc_id for doc_id in doc_ids}
            for future in as_completed(futures):
                if future.result():
                    success_total += 1
                else:
                    failed_total += 1

        logger.info(
            "Done. backfilled=%s  failed=%s  total=%s",
            success_total, failed_total, total,
        )
