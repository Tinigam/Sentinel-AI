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

`POST /api/v1/ingest` runs both RSS ingestion and official page discovery. For server-rendered sites that embed links in JSON payloads instead of anchors (for example Next.js pages), discovery falls back to extracting same-domain announcement URLs from the raw document; titles are then read from each detail page's `<title>`. Pure client-rendered shells (for example sr.mihoyo.com) still yield nothing and need site-specific adapters.
## Bilibili official accounts

`bilibili_accounts` in `sources.yaml` maps each game to its verified official Bilibili account (`mid`, resolved via the search API against `official_verify` labels). Each recent video becomes an `official_announcement` article whose content is the video description plus a `热门评论` section of top comments — the player-voice signal for sentiment analysis.

The collector uses the public web API with WBI signing. The comment endpoint allows anonymous access; the account video list may be rejected by risk control (HTTP 412 / code -352) from datacenter or overseas IPs, in which case the account is skipped and counted in `failed_accounts`. Setting `BILIBILI_COOKIE` (a logged-in `SESSDATA=...` cookie, placed in the repo-root `.env` that Compose substitutes) makes collection reliable. Requests are throttled and each video is deduplicated by its canonical URL, so repeated ingestion only fetches comments for new videos.
## Tieba forums

`tieba_forums` in `sources.yaml` maps each game to its Baidu Tieba forum (`kw` is the forum name without the 吧 suffix). Each collected thread becomes a `community_post` article whose content is the opening post (floor 1) plus a `热门回复` section of the most-liked replies — the unfiltered player-voice signal that official channels lack.

The collector uses the Tieba client API (`c.tieba.baidu.com`) with the client sign scheme (sorted `k=v` pairs suffixed with `tiebaclient!!!`, MD5-hashed) and anonymous client identity parameters; the web frontend is gated behind a 403 safety check and is not used. Note the thread-content endpoint takes the thread id as `kz`, not `tid`. Threads with fewer than `TIEBA_MIN_REPLIES` replies are skipped as drive-by posts (`skipped_threads` in the ingest report). Requests are throttled to ~2s and each thread is deduplicated by its canonical URL, so repeated ingestion only fetches new threads. For distortion detection the first 30 reply floors of every new thread are stored in `community_comments` (`platform="tieba"`, `user_mid` carries the author portrait id); threads with 30+ stored replies get a `comment_metrics` report like the Bilibili ones below.
## Comment distortion detection

For the newest video of each configured account, the collector deep-crawls top-level comments in time order (`BILIBILI_COMMENT_PAGES`, default 25 pages ≈ 500 comments) into `community_comments` and stores a distortion report on the article's `comment_metrics`:

- `gini`, `top1_share`, `top5_share`: concentration of comments across users (a vocal-minority detector; organic sections rarely exceed 0.7 top-5% share).
- `template_share` and `top_templates`: copypasta brigading — comments clustered by normalized-text hash (emoji, punctuation and whitespace stripped), clusters of 3+ counted once.
- `sentiment_raw` vs `sentiment_user_voted` (one user one vote) vs `sentiment_like_weighted`: divergence between these three readings marks manipulated volume.
- `distortion_flags`: `copypasta_brigade` (template share > 0.3), `high_concentration` (top-5% users > 0.7), `like_divergence` (raw negative share exceeds like-weighted by > 0.15).

These metrics are confidence metadata, not filters: flagged articles stay indexed, but dashboards and RAG answers should treat their raw comment volume as unreliable.
