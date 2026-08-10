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

## Reranker (optional, currently disabled)

Setting `RERANK_MODEL` (e.g. `gte-rerank-v2` on `RERANK_BASE_URL`) re-orders the top-30 RRF shortlist with a cross-encoder; failures keep RRF order. Union-pool evaluation on 2026-08-10 (512 graded pairs, 30 questions) measured it as a **net regression** on this corpus: Recall@10 0.641 vs 0.692 for plain RRF, because rerank documents derived from chunks were title-echo noise. It stays disabled (`RERANK_MODEL=` empty) until re-validated with better documents or a stronger model.

## Measured baselines (union-pool annotation, 2026-08-10)

| Retrieval | Recall@10 | nDCG@10 |
| --- | ---: | ---: |
| local-hash.v1 | 0.432 | 0.390 |
| text-embedding-v4 (RRF) | 0.692 | 0.695 |
| + gte-rerank-v2 | 0.641 | 0.629 |
| + LLM multi-query | 0.685 | 0.698 |

Neither rerank nor multi-query expansion cleared the 0.80 Recall@10 gate: the residual gap is corpus coverage (e.g. no negative articles at all for some games) and aggregation-style questions that per-article retrieval cannot answer, not ranking quality. Earlier per-run scores were inflated by candidate-pool bias — annotations must cover the union of compared runs' candidates.

Corpus scaling confirms the coverage hypothesis (same final 638-pair annotation file): enlarging the corpus from 750 to 915 articles (+22%, 25 videos per bilibili account) lifted Recall@10 from 0.460 to 0.637 and nDCG@10 from 0.563 to 0.733. Remaining gaps need source diversity (player communities with critical sentiment), not more ranking tuning.

## Operational Commands

```text
POST /api/v1/index   # create or rebuild chunk embeddings for relevant articles
GET /api/v1/search?query=崩坏%20星穹铁道&topic=honkai-star-rail
```

`POST /api/v1/index` is development-only or protected by `X-Ingest-Key` outside development.