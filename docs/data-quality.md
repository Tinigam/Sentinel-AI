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
## NGA forums

`nga_forums` in `sources.yaml` maps each game to its NGA board (`fid`, verified against a logged-in session via `thread.php?fid=<fid>&__output=8`). Each collected thread becomes a `community_post` article whose content is the opening post plus a reply-count line; the first reply floors (single read.php page, ~19 floors) are stored in `community_comments` (`platform="nga"`).

The collector uses the legacy web JSON view (`__output=8`, GB18030-encoded, parsed with `strict=False` because payloads embed raw control characters). Anonymous visitors get HTTP 403 "访客不能直接访问", so the lane requires the stored NGA session: when `sessions/nga.json` is missing or expired the lane logs a warning and skips instead of failing. Threads are deduplicated by their full `read.php?tid=<tid>` URL (the thread id lives in the query string, so the query-stripped canonical form cannot be used). Some threads return truncated JSON from read.php; those are still stored (title + reply count) with their floors skipped and counted in `failed_items`.

## Weibo search

`weibo_queries` in `sources.yaml` searches the desktop web search (`https://s.weibo.com/weibo?q=<keyword>`, one page ≈ 20 cards) per game with the stored Weibo session. The m.weibo.cn mobile API returns empty results for this session and is deliberately not used. Each result card becomes a `community_post` article whose content is the post text plus a 转发/评论/赞 stats line; relative timestamps (今天/分钟前/MM月DD日) are parsed best-effort into `published_at`. Cards are deduplicated by their `weibo.com/<uid>/<bid>` URL with query parameters stripped. No per-post comments are fetched. When the session is missing or the search page stops returning result cards, the lane logs a warning and skips.

## Miyoushe forums

`miyoushe_forums` in `sources.yaml` maps a game to a miyoushe board (`gids` identifies the game — 2=原神, 6=崩坏：星穹铁道; `forum_id` the board — the general-chat boards 酒馆=26 and 候车室=52 carry the best player-voice signal). Only miHoYo games exist on miyoushe. The collector uses the anonymous `bbs-api.miyoushe.com` post list; the post `content` JSON string's `describe` field becomes the article body, plus a 回复/赞/浏览 stats line. Posts are deduplicated by their `www.miyoushe.com/<game>/article/<post_id>` URL. No login or per-post comments are involved.

## Comment distortion detection

For the newest video of each configured account, the collector deep-crawls top-level comments in time order (`BILIBILI_COMMENT_PAGES`, default 25 pages ≈ 500 comments) into `community_comments` and stores a distortion report on the article's `comment_metrics`:

- `gini`, `top1_share`, `top5_share`: concentration of comments across users (a vocal-minority detector; organic sections rarely exceed 0.7 top-5% share).
- `template_share` and `top_templates`: copypasta brigading — comments clustered by normalized-text hash (emoji, punctuation and whitespace stripped), clusters of 3+ counted once.
- `sentiment_raw` vs `sentiment_user_voted` (one user one vote) vs `sentiment_like_weighted`: divergence between these three readings marks manipulated volume.
- `distortion_flags`: `copypasta_brigade` (template share > 0.3), `high_concentration` (top-5% users > 0.7), `like_divergence` (raw negative share exceeds like-weighted by > 0.15).

These metrics are confidence metadata, not filters: flagged articles stay indexed, but dashboards and RAG answers should treat their raw comment volume as unreliable.
