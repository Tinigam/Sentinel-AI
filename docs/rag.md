# Evidence-grounded RAG

## V1 Answer Contract

`POST /api/v1/ask` consumes a user question with optional game and sentiment filters. It invokes hybrid retrieval, builds a bounded evidence set, and returns a structured response containing answer text, claim-level citation IDs, and source metadata.

Every citation ID is validated against the retrieved source set before the response is returned. When no source survives retrieval, the API sets `insufficient_evidence: true` and explicitly declines to make unsupported claims.

## Current Generator

The running V1 generator is `extractive.v1`. It produces a concise evidence summary by quoting bounded snippets from the top retrieved sources. This is deliberate: it keeps the default Docker workflow functional without an API key and guarantees that every displayed claim remains tied to an actual source.

## LLM Upgrade Path

A compatible LLM provider can replace `extractive.v1` after `LLM_BASE_URL`, `LLM_MODEL`, and the secret API key are configured. The provider must receive only the selected evidence records, return strict JSON, and pass the same citation validator. It must never expose chain-of-thought or accept instructions embedded in article text.

Required output fields remain:

```text
answer
summary_points[].claim
summary_points[].citation_ids
insufficient_evidence
```

Evaluation must measure Citation Correctness, Citation Coverage, Citation Relevance, and refusal accuracy for insufficient-evidence queries.