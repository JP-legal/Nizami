# Improving Data Quality via Structured Metadata

## Context

The clean data pipeline produces JSON documents with two distinct payloads:
- `clean_text` — the normalized Arabic body used for chunking and embedding
- `metadata` — structured fields extracted during processing

Currently, `RagSourceDocument` stores only `title`, `s3_key`, `description`, and embedding fields. The rich `metadata` object is embedded inside the S3 JSON but never surfaced to the database. This document explains what value that metadata holds and how exposing it improves both retrieval quality and LLM response quality.

---

## Metadata Fields and Their Value

```json
{
  "doc_type": "نزع",
  "title": "نزع ملكية من اجل تعزيز موثوقية الشبكة الكهربائية في منطقة القصيم",
  "date_hijri_dual": "1445-2-23",
  "date_gregorian": "2023-09-08",
  "entity": "وزير الطاقة",
  "decision_number": "4115/440201",
  "decision_date_hijri": "18/11/1444ه",
  "context": "...",
  "decision": "...",
  "incomplete_flag": false,
  "is_duplicate_filename": false,
  "format_detection": { "confidence": 1.0, "match_reason": "filename_prefix" }
}
```

---

## How Metadata Benefits the Database

### 1. Structured Filtering Before Vector Search (Pre-filtering)

Vector search is a nearest-neighbor approximation. Without pre-filtering, every query scans all chunks regardless of relevance. Storing metadata as indexed columns allows SQL `WHERE` clauses to narrow the candidate set before cosine similarity is computed — making retrieval both faster and more precise.

| Metadata Field | DB Column | Enables |
|---|---|---|
| `doc_type` | `doc_type VARCHAR` | Filter: "only نزع documents" |
| `entity` | `entity VARCHAR` | Filter: "decisions by وزير الطاقة only" |
| `date_gregorian` | `date_gregorian DATE` | Range queries: "decisions after 2023-01-01" |
| `decision_number` | `decision_number VARCHAR` | Exact lookup by decision ID |
| `incomplete_flag` | `incomplete_flag BOOLEAN` | Exclude incomplete documents from search |
| `is_duplicate_filename` | `is_duplicate BOOLEAN` | Deduplicate at query time |
| `format_detection.confidence` | `format_confidence FLOAT` | Filter low-confidence parsed docs |

### 2. Deduplication and Quality Gates

`incomplete_flag` and `is_duplicate_filename` let you exclude low-quality records at the database level rather than returning them to the LLM and hoping it ignores them. A partial document returned to the LLM will produce a partial or hallucinated answer.

### 3. Pre-extracted Semantic Segments as Dedicated Chunks

The pipeline already splits the document into two meaningful parts:
- `context`: the legal authority and basis for the decision
- `decision`: the actual ordered clauses

Storing these as dedicated `RagSourceDocumentChunk` records (with a `segment_type` column) instead of relying on arbitrary character-boundary splits from `RecursiveCharacterTextSplitter` means:
- The context clause never gets cut mid-sentence
- The decision clause is always retrieved as a unit
- Retrieval can specifically target `segment_type = 'decision'` when the user asks "what was decided"

### 4. Chunk-Level Metadata Inheritance

Currently `RagSourceDocumentChunk` stores only `content`, `embedding`, and `chunk_index`. Attaching inherited metadata (`doc_type`, `entity`, `date_gregorian`) to each chunk means chunk-level vector search results carry enough context to filter without joining back to the parent document. This matters for pgvector queries that use `ORDER BY embedding <=> $1 LIMIT k` — the metadata is already in the row.

---

## How Metadata Benefits the LLM

### 1. Grounding via System Context

When the LLM receives retrieved chunks, it currently receives raw Arabic text with no structural anchor. Prepending structured metadata to each retrieved chunk gives the model a reliable frame:

```
[وثيقة: نزع ملكية | الجهة: وزير الطاقة | التاريخ: 2023-09-08 | القرار: 4115/440201]
وبناء علي الصلاحيات المخولة له نظاما...
```

This lets the model:
- Correctly attribute decisions to the right authority without inferring from body text
- Answer "when was this decision issued?" without scanning the full text
- Distinguish between multiple retrieved documents that use similar Arabic phrasing

### 2. Accurate Date Reasoning

Dates in Arabic legal documents appear in Hijri calendar (`1445-2-23`) and Gregorian (`2023-09-08`). The raw text includes both but the LLM must parse them in context, which is error-prone. Having `date_gregorian` as a structured field that is explicitly passed as metadata eliminates this parsing ambiguity. The model gets an unambiguous ISO date alongside the chunk.

### 3. Routing Queries to the Right Document Type

`doc_type` acts as a document classifier. If the system knows query intent maps to `doc_type = نزع` (expropriation), it can retrieve only from that subset rather than returning unrelated decision types. This is a metadata-based routing layer that reduces irrelevant context tokens in the LLM prompt.

### 4. Decision Number as a Verifiable Citation

`decision_number` is the canonical identifier for a legal decision. When the LLM cites a decision in its response, it should cite this number. Without it in the metadata, the model has to extract it from body text — which it may get wrong if multiple decision numbers appear or OCR artifacts are present. Passing it explicitly as metadata makes citations reliable and verifiable.

### 5. Confidence-Aware Retrieval

`format_detection.confidence` is a machine-generated confidence score for how well the pipeline parsed this document. A score of `1.0` means the format was unambiguously detected; lower scores indicate uncertain parsing. The LLM prompt can include this signal:

```
[تحذير: ثقة التحليل منخفضة — قد تكون البيانات المستخرجة غير مكتملة]
```

This lets the model hedge its answer appropriately rather than asserting partial data with full confidence.

---

## Recommended Model Changes

### `RagSourceDocument`

Add the following columns populated at embedding time from the S3 JSON `metadata` object:

```python
doc_type = models.CharField(max_length=100, null=True, blank=True, db_index=True)
entity = models.CharField(max_length=255, null=True, blank=True, db_index=True)
date_gregorian = models.DateField(null=True, blank=True, db_index=True)
date_hijri = models.CharField(max_length=20, null=True, blank=True)
decision_number = models.CharField(max_length=100, null=True, blank=True, db_index=True)
decision_date_hijri = models.CharField(max_length=50, null=True, blank=True)
incomplete_flag = models.BooleanField(default=False, db_index=True)
is_duplicate = models.BooleanField(default=False, db_index=True)
format_confidence = models.FloatField(null=True, blank=True)
source = models.CharField(max_length=100, null=True, blank=True, db_index=True)
```

### `RagSourceDocumentChunk`

Add a `segment_type` column to distinguish pre-extracted semantic segments from character-split chunks:

```python
segment_type = models.CharField(
    max_length=50,
    null=True,
    blank=True,
    choices=[('context', 'Context'), ('decision', 'Decision'), ('chunk', 'Chunk')],
    db_index=True
)
```

And inherit key metadata from the parent for filter-free retrieval:

```python
doc_type = models.CharField(max_length=100, null=True, blank=True)
entity = models.CharField(max_length=255, null=True, blank=True)
date_gregorian = models.DateField(null=True, blank=True)
```

### `embed_rag_source_documents.py` Changes

After fetching the S3 JSON:
1. Parse `metadata` and write structured fields to `RagSourceDocument` columns
2. Create two dedicated chunks from `metadata.context` and `metadata.decision` with `segment_type` set accordingly, before running the character splitter on `clean_text`
3. Skip embedding documents where `incomplete_flag=True` unless `--force` is passed
4. Skip embedding documents where `is_duplicate_filename=True` unless `--include-duplicates` is added as a new flag

---

## Should Metadata Be Baked into Chunk Embeddings or Stay Separate?

This is the most important architectural decision in the pipeline. The two options:

**Option A — Include metadata in the chunk text before embedding**
Prepend structured fields to each chunk before calling `embeddings.embed_documents()`:
```
[نوع: نزع | الجهة: وزير الطاقة | التاريخ: 2023-09-08 | القرار: 4115/440201]
وبناء علي الصلاحيات المخولة له نظاما...
```

**Option B — Keep chunk embeddings pure, store metadata as DB columns, re-rank after retrieval**
Embed only `clean_text` chunks as today. Store metadata as indexed columns. After cosine similarity, apply a re-ranking score:
```
final_score = α × cosine_score + (1 - α) × freshness_score
```

### Why Option A is the wrong choice here

The chunks in this pipeline are 800 characters of dense Arabic legal text. Prepending even 100 characters of metadata to that creates an embedding that is a mixture of two different semantic signals — the document's administrative identity and its legal content. This causes three problems:

1. **Embedding pollution on short chunks.** The last chunk of any document is almost always shorter than 800 characters. A 150-char tail chunk with 100 chars of metadata prefix produces an embedding that is majority metadata, not content. Two chunks from completely different decisions but the same `entity` will appear more similar to each other than they should.

2. **Baked-in semantics cannot be changed without re-embedding.** If `entity` is corrected in the S3 JSON, or `doc_type` classification is updated, every chunk must be re-embedded at cost. With metadata as a DB column, correction is a single `UPDATE`.

3. **Cross-type retrieval breaks.** A user asking a general question about expropriation procedure gets chunks that are partially ranked by whether they happen to share the same entity tag, not by whether the legal text is relevant.

### Why freshness-only re-ranking is also insufficient

For legal documents, the newest decision is not always the most relevant one. A user may ask about a decision that was issued in 2021 and has since been amended — the LLM needs both the original and the amendment. Ranking purely by freshness would suppress the original. The right behavior is:

- Always surface `date_gregorian` in the chunk context window so the LLM can reason about chronology itself
- Apply a mild freshness boost (low α, e.g. 0.15) only as a tiebreaker when cosine scores are close, not as a primary ranking signal

### Recommended approach: two-stage retrieval

The codebase already generates a `description_embedding` per `RagSourceDocument` — this is the correct place to encode metadata semantics. The pipeline should work in two stages:

**Stage 1 — Document-level match via `description_embedding`**

When generating the description (the call to `generate_description_for_text`), pass metadata context into the prompt so the LLM-generated description naturally references the doc_type, entity, and date. The resulting `description_embedding` encodes that semantic identity without polluting chunk embeddings.

```
# What the description generation prompt should include:
"نوع الوثيقة: نزع ملكية. الجهة: وزير الطاقة. التاريخ: 2023-09-08. رقم القرار: 4115/440201.
النص: وبناء علي الصلاحيات المخولة له..."
```

Use this embedding to retrieve the top-N most relevant *documents* from `RagSourceDocument`. Apply pre-filters here (`doc_type`, `entity`, `incomplete_flag=false`). Apply the freshness boost here too — at the document level, not the chunk level.

**Stage 2 — Chunk-level match within candidate documents**

Within the top-N documents returned by Stage 1, run cosine similarity against `RagSourceDocumentChunk.embedding` to find the exact passages. These embeddings are pure `clean_text` — no metadata noise.

**What gets passed to the LLM**

Each retrieved chunk is wrapped with its parent document's metadata before being placed in the context window:

```
[نوع: نزع | الجهة: وزير الطاقة | التاريخ: 2023-09-08 | القرار: 4115/440201]
وبناء علي الصلاحيات المخولة له نظاما, وبعد الاطلاع علي البند (ثالثا)...
```

The metadata is not embedded — it is injected into the prompt at query time from the DB columns. The LLM gets full document identity as structured context without that structure having distorted the chunk vectors.

### Decision matrix

| | Bake metadata into chunk embedding | Metadata as DB column + re-rank |
|---|---|---|
| Chunk embedding quality | Degraded (mixed signal) | Pure content semantics |
| Metadata correction cost | Re-embed all chunks | Single DB UPDATE |
| Cross-type retrieval | Broken (entity bias) | Works correctly |
| Date/freshness reasoning | Implicit (in vector) | Explicit (in prompt context) |
| Filtering by entity/doc_type | Approximate | Exact (SQL WHERE) |
| Recommended | No | Yes |

---

## Summary

| Without Metadata | With Metadata |
|---|---|
| Vector search scans all chunks | Pre-filter by doc_type, entity, date |
| LLM infers document structure from text | LLM receives explicit structured frame |
| Duplicate/incomplete docs silently returned | Excluded at query time via DB flags |
| Arbitrary character splits may break context/decision boundary | Semantic segments stored as dedicated chunks |
| Citations rely on LLM text extraction | Decision number passed explicitly as metadata |
| All documents equally trusted | Confidence score signals parsing quality |
