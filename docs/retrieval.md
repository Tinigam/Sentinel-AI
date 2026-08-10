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

Each relevant article is currently stored as one bounded text chunk and indexed with pgvector cosine distance. The embedding provider is chosen per process by API-key presence in `app.services.indexing.embed_texts`:

- With `OPENAI_API_KEY` set, chunks and queries go through the OpenAI-compatible endpoint (`LLM_BASE_URL` + `EMBEDDING_MODEL`, e.g. Aliyun Bailian `text-embedding-v4` at dimension 1536).
- Without a key, the deterministic `local-hash.v1` token-hashing vector keeps Docker Compose and unit tests fully runnable offline.

Remote failures raise `EmbeddingError` instead of falling back mid-run, so the index never mixes vector spaces. Switching providers or dimensions requires reindexing all chunks; a dimension other than 1536 also requires a migration for the `vector(1536)` schema.
5. Re-run Recall@K, nDCG@K, Citation Correctness, Coverage, and Relevance evaluations.

## RRF

For each article rank `r` in a retrieval list, Sentinel-AI adds `1 / (60 + r)` to its score. Results from full-text and vector retrieval are combined by article ID, preventing multiple chunks from a single article from crowding out source diversity.

## Operational Commands

```text
POST /api/v1/index   # create or rebuild chunk embeddings for relevant articles
GET /api/v1/search?query=崩坏%20星穹铁道&topic=honkai-star-rail
```

`POST /api/v1/index` is development-only or protected by `X-Ingest-Key` outside development.