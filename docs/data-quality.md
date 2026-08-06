# Data Quality

Sentinel-AI separates collection transport from the evidence quality of each item.

## Source metadata

`config/sources.yaml` defines every feed with:

- `source_type`: `official`, `media`, or `aggregator`.
- `trust_tier`: `verified`, `curated`, or `aggregated`.
- `feed_url` for a direct feed, or `query` for a Google News RSS query.

A Google News feed is only a transport mechanism; the stored article URL remains the source URL presented to users.

## Article taxonomy

| Content type | Included in Dashboard, RAG and index | Rule |
| --- | --- | --- |
| `official_announcement` | Yes | Collected from a configured official source |
| `media_news` | Yes | Default non-official news item |
| `guide` | No | Guide, build, team-composition or walkthrough signal |
| `esports` | No | Tournament, league, team or match-report signal |

The classifier deliberately favors exclusion for obvious gameplay guides and esports reports. It does not delete them; they remain traceable in PostgreSQL, but `is_intelligence=false` prevents them from affecting sentiment trends, hybrid retrieval and RAG answers.

## Operations

After changing source configuration or classification rules, run:

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/v1/classify
Invoke-RestMethod -Method Post http://localhost:8000/api/v1/index
```

The first command recomputes article types and creates missing sentiments for eligible articles. The second removes ineligible chunks and rebuilds embeddings for eligible, topic-linked articles.
## Official page discovery

official_pages supports static public announcement lists. Each configured page has a first-party URL, a topic, and official / erified provenance metadata. The collector intentionally accepts only same-domain announcement-like links; dynamic sites that render no links are skipped rather than bypassed.
