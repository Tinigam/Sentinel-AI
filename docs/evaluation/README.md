# V1 Evaluation Baseline

This directory contains the versioned human-annotation inputs for Sentinel-AI.
The question set is intentionally separated from run results: it can be reused after each ingestion or retrieval-model change.

## Protocol

1. Ingest and index a fixed snapshot. Record its article count, collection time, source list, commit SHA and configuration checksum.
2. For each question in `questions.v1.json`, a reviewer records relevant `article_id` values and the expected evidence type.
3. Run `POST /api/v1/search` and `POST /api/v1/ask` with the snapshot.
4. Record Recall@5, Recall@10, nDCG@10, citation correctness, citation coverage and citation relevance.
5. Label sentiment on a stratified random sample of at least 100 `(article_id, topic_id)` pairs; report macro-F1 and a confusion matrix.

Do not treat a community opinion as a verified fact. The `source_type` of every judgment must be retained in the result file.

## Acceptance gates for V1

| Metric | Target |
| --- | ---: |
| Retrieval Recall@10 | >= 0.80 |
| Citation correctness | >= 0.90 |
| Citation coverage | >= 0.85 |
| Sentiment macro-F1 | Baseline reported; no release claim before annotation |
| Invalid citation IDs | 0 |

`questions.v1.json` is a seed set. It becomes a scored benchmark only after article IDs are annotated for a frozen corpus.