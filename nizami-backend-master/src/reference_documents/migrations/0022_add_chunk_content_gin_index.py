# GIN index on RagSourceDocumentChunk.content to accelerate full-text keyword search.
#
# The FilteredRetriever._keyword_search_chunks() function uses PostgreSQL tsvector
# to find chunks containing exact legal terms (law names, article numbers, penalty
# amounts) that may score low on cosine similarity alone.
#
# Without this index every tsvector query does a full table scan; with it the
# planner uses the GIN index for fast inverted-index lookup.
#
# Uses the 'simple' configuration so it works on any PostgreSQL installation
# regardless of whether the 'arabic' text-search config is installed.
# ts_rank() and plainto_tsquery() calls in the retriever can still use 'arabic'
# config at query time — the index just needs a consistent config for storage.
from django.db import migrations


class Migration(migrations.Migration):
    # CONCURRENTLY cannot run inside a transaction — disable the migration wrapper.
    atomic = False

    dependencies = [
        ('reference_documents', '0021_ragsourcedocument_metadata_columns'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS rag_chunk_content_gin_idx
                ON reference_documents_ragsourcedocumentchunk
                USING GIN (to_tsvector('simple', content));
            """,
            reverse_sql="""
                DROP INDEX CONCURRENTLY IF EXISTS rag_chunk_content_gin_idx;
            """,
        ),
    ]
