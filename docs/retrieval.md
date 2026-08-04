# Hybrid Retrieval Baseline

## V1 Implementation

Sentinel-AI V1 implements hybrid retrieval in PostgreSQL:

```text
PostgreSQL Full-Text Search Top 20
                +
pgvector Chunk Search Top 20
                ↓
Reciprocal Rank Fusion (k = 60)
                ↓
Top-K source articles
```

`GET /api/v1/search` accepts `query`, optional `topic`, optional `sentiment`, and `limit`. It returns the retrieval method, candidate count, RRF score, source metadata, and supporting snippet. This endpoint is the retrieval contract used by the future RAG answer service.

## Full-Text Search

Migration `0002_search_and_vector_indexes` adds `articles.search_vector`, a trigger that updates it on title/summary/content changes, and a GIN index. Queries use PostgreSQL `websearch_to_tsquery('simple', query)` and `@@` matching.

The `simple` configuration is a portable baseline. Production Chinese quality should replace it with a verified Chinese tokenizer extension or an application-level Chinese tokenization pipeline before rebuilding the GIN index. This change must be benchmarked against the evaluation dataset.

## Vector Search

Each relevant article is currently stored as one bounded text chunk and indexed with pgvector cosine distance. The V1 development provider is `local-hash.v1`: a deterministic token-hashing vector that keeps Docker Compose fully runnable without an API key.

It is intentionally a functional development baseline, not a claim of production-quality semantic embeddings. The provider boundary is `app.services.indexing.embed`. To upgrade:

1. Add a provider configuration and secret-backed API key.
2. Call the selected embedding API for every chunk and query.
3. Ensure output dimension matches the `vector(1536)` schema, or add a migration for a new dimension.
4. Reindex all chunks.
5. Re-run Recall@K, nDCG@K, Citation Correctness, Coverage, and Relevance evaluations.

## RRF

For each article rank `r` in a retrieval list, Sentinel-AI adds `1 / (60 + r)` to its score. Results from full-text and vector retrieval are combined by article ID, preventing multiple chunks from a single article from crowding out source diversity.

## Operational Commands

```text
POST /api/v1/index   # create or rebuild chunk embeddings for relevant articles
GET /api/v1/search?query=崩坏%20星穹铁道&topic=honkai-star-rail
```

`POST /api/v1/index` is development-only or protected by `X-Ingest-Key` outside development.