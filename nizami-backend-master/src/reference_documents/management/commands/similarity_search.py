"""
Two modes:

1. Query search (query argument provided):
   Embed the query text and return the top-K most similar documents.

   python manage.py similarity_search "قرار وزارة الثقافة" --top 10

2. All-pairs analysis (no query):
   For every embedded document find its nearest neighbours, then surface the
   most similar pairs across the entire corpus — useful for duplicate/near-
   duplicate detection.

   python manage.py similarity_search --top 20 --min-score 0.3
"""
from django.core.management.base import BaseCommand, CommandParser
from pgvector.django import CosineDistance

from src.reference_documents.models import RagSourceDocument
from src.settings import embeddings

_ONLY_FIELDS = ("id", "title", "doc_type", "s3_bucket", "s3_key", "description_embedding")


def _s3_url(doc):
    if doc.s3_bucket and doc.s3_key:
        return f"https://{doc.s3_bucket}.s3.amazonaws.com/{doc.s3_key}"
    return "-"


class Command(BaseCommand):
    help = (
        "Cosine-similarity search over RagSourceDocuments. "
        "Pass a query string to search, or omit it to run a corpus-wide similar-pairs analysis."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "query",
            nargs="?",
            type=str,
            default=None,
            help="Query text to embed and search. Omit to run all-pairs analysis.",
        )
        parser.add_argument(
            "--top",
            type=int,
            default=10,
            help="Number of results to return (default 10).",
        )
        parser.add_argument(
            "--min-score",
            type=float,
            default=None,
            help=(
                "Only show results whose cosine distance is ≤ this value "
                "(0 = identical, 2 = opposite). "
                "For all-pairs mode a sensible starting point is 0.3."
            ),
        )
        parser.add_argument(
            "--neighbours",
            type=int,
            default=5,
            help="(All-pairs mode) Nearest neighbours to fetch per document (default 5).",
        )

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        query = options["query"]

        if query:
            self._query_search(query, top_k=options["top"], min_score=options["min_score"])
        else:
            self._all_pairs_analysis(
                top_k=options["top"],
                neighbours=options["neighbours"],
                min_score=options["min_score"],
            )

    # ------------------------------------------------------------------
    # Mode 1 — query search
    # ------------------------------------------------------------------
    def _query_search(self, query, *, top_k, min_score):
        self.stdout.write(f'Embedding query: "{query}" …')
        query_vector = embeddings.embed_query(query)

        qs = (
            RagSourceDocument.objects.filter(
                description_embedding__isnull=False,
                is_embedded=True,
            )
            .annotate(distance=CosineDistance("description_embedding", query_vector))
            .order_by("distance")
        )

        if min_score is not None:
            qs = qs.filter(distance__lte=min_score)

        results = list(qs[:top_k])

        if not results:
            self.stdout.write(self.style.WARNING("No results found."))
            return

        self._print_header("Query Search Results")
        for rank, doc in enumerate(results, start=1):
            self.stdout.write(
                f"[{rank}] Distance: {doc.distance:.4f}  |  ID: {doc.id}  |  Type: {doc.doc_type or '-'}\n"
                f"    Title : {doc.title or '-'}\n"
                f"    S3    : {_s3_url(doc)}\n"
            )

    # ------------------------------------------------------------------
    # Mode 2 — corpus-wide similar-pairs analysis
    # ------------------------------------------------------------------
    def _all_pairs_analysis(self, *, top_k, neighbours, min_score):
        docs = list(
            RagSourceDocument.objects.filter(
                description_embedding__isnull=False,
                is_embedded=True,
            ).only(*_ONLY_FIELDS)
        )

        if not docs:
            self.stdout.write(self.style.WARNING("No embedded documents found."))
            return

        self.stdout.write(
            f"Running all-pairs analysis on {len(docs)} documents "
            f"(neighbours={neighbours}) …"
        )

        seen = set()
        pairs = []

        for doc in docs:
            neighbours_qs = (
                RagSourceDocument.objects.filter(
                    description_embedding__isnull=False,
                    is_embedded=True,
                )
                .exclude(id=doc.id)
                .annotate(distance=CosineDistance("description_embedding", doc.description_embedding))
                .order_by("distance")[:neighbours]
            )

            for neighbour in neighbours_qs:
                pair_key = frozenset([doc.id, neighbour.id])
                if pair_key in seen:
                    continue
                seen.add(pair_key)

                if min_score is not None and neighbour.distance > min_score:
                    continue

                pairs.append((neighbour.distance, doc, neighbour))

        if not pairs:
            self.stdout.write(self.style.WARNING("No similar pairs found (try raising --min-score)."))
            return

        pairs.sort(key=lambda x: x[0])
        pairs = pairs[:top_k]

        self._print_header(f"Top {len(pairs)} Most Similar Document Pairs")
        for rank, (dist, doc_a, doc_b) in enumerate(pairs, start=1):
            self.stdout.write(
                f"[{rank}] Distance: {dist:.4f}\n"
                f"  A  ID: {doc_a.id}  |  Type: {doc_a.doc_type or '-'}\n"
                f"     Title : {doc_a.title or '-'}\n"
                f"     S3    : {_s3_url(doc_a)}\n"
                f"  B  ID: {doc_b.id}  |  Type: {doc_b.doc_type or '-'}\n"
                f"     Title : {doc_b.title or '-'}\n"
                f"     S3    : {_s3_url(doc_b)}\n"
            )

        self.stdout.write(f"Total unique pairs evaluated: {len(seen)}")

    # ------------------------------------------------------------------
    def _print_header(self, title):
        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write(f"  {title}")
        self.stdout.write(f"{'=' * 60}\n")
