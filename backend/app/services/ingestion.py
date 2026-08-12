import hashlib
import html
import logging
import re
from datetime import UTC, datetime
from urllib.parse import urlencode, urlparse, urlunparse

import feedparser
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.collectors.bilibili import BilibiliClient, BilibiliError, format_comments_section
from app.collectors.miyoushe import MiyousheClient, MiyousheError
from app.collectors.nga import NgaClient, NgaError
from app.collectors.tieba import TiebaClient, TiebaError, format_replies_section
from app.collectors.weibo import WeiboClient, WeiboError
from app.collectors.official_pages import discover_announcements, extract_title, fetch_html, html_to_text
from app.models.entities import Article, ArticleTopic, CommunityComment, Source, Topic
from app.services.community import compute_comment_metrics
from app.services.content import classify_content_type
from app.services.sentiment import classify_article
from app.services.session_auth import load_cookie_header

logger = logging.getLogger(__name__)


def canonical(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))


def published(entry: object) -> datetime | None:
    value = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    return datetime(*value[:6], tzinfo=UTC) if value else None


def sync_topics(db: Session) -> list[Topic]:
    with get_settings().topics_config_path.open(encoding="utf-8") as file:
        configured = yaml.safe_load(file)["topics"]
    topics = []
    for item in configured:
        topic = db.scalar(select(Topic).where(Topic.slug == item["slug"]))
        if topic is None:
            topic = Topic(slug=item["slug"], display_name=item["display_name"])
            db.add(topic)
        topic.aliases = item.get("aliases", [])
        topic.keywords = item.get("keywords", [])
        topics.append(topic)
    db.flush()
    return topics


def google_news_feed_url(query: str) -> str:
    return "https://news.google.com/rss/search?" + urlencode(
        {"q": query, "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"}
    )


def load_rss_sources() -> list[dict[str, str]]:
    settings = get_settings()
    if not settings.sources_config_path.exists():
        return [{"name": settings.rss_source_name, "feed_url": settings.rss_feed_url, "source_type": "aggregator", "trust_tier": "aggregated"}]
    with settings.sources_config_path.open(encoding="utf-8") as file:
        configured = yaml.safe_load(file) or {}
    sources = []
    for item in configured.get("rss_sources", []):
        if not item.get("enabled", True):
            continue
        feed_url = item.get("feed_url") or google_news_feed_url(item["query"])
        sources.append(
            {
                "name": item["name"],
                "feed_url": feed_url,
                "source_type": item.get("source_type", "aggregator"),
                "trust_tier": item.get("trust_tier", "aggregated"),
            }
        )
    return sources


def get_or_create_source(db: Session, config: dict[str, str]) -> Source:
    source = db.scalar(select(Source).where(Source.feed_url == config["feed_url"]))
    if source is None:
        source = db.scalar(select(Source).where(Source.name == config["name"]))
    if source is None:
        source = Source(name=config["name"])
        db.add(source)
    source.name = config["name"]
    source.domain = urlparse(config["feed_url"]).netloc
    source.feed_url = config["feed_url"]
    source.source_type = config["source_type"]
    source.trust_tier = config["trust_tier"]
    db.flush()
    return source


def ingest_rss(db: Session) -> dict[str, int]:
    topics = sync_topics(db)
    inserted = duplicate = classified = failed_sources = 0
    sources = load_rss_sources()
    for config in sources:
        source = get_or_create_source(db, config)
        feed = feedparser.parse(config["feed_url"])
        if feed.bozo and not feed.entries:
            failed_sources += 1
            continue
        for entry in feed.entries:
            title = (getattr(entry, "title", "") or "").strip()
            url = (getattr(entry, "link", "") or "").strip()
            if not title or not url:
                continue
            summary = re.sub(
                r"<[^>]+>",
                " ",
                html.unescape(getattr(entry, "summary", "") or getattr(entry, "description", "") or ""),
            ).strip()
            clean_url = canonical(url)
            digest = hashlib.sha256((title + summary).encode()).hexdigest()
            exists = db.scalar(
                select(Article.id).where(
                    (Article.original_url == url)
                    | (Article.canonical_url == clean_url)
                    | (Article.content_hash == digest)
                )
            )
            if exists:
                duplicate += 1
                continue
            content_classification = classify_content_type(title, summary, source.source_type)
            article = Article(
                source_id=source.id,
                title=title,
                summary=summary,
                content=summary,
                original_url=url,
                canonical_url=clean_url,
                source_name=source.name,
                source_domain=source.domain,
                published_at=published(entry),
                title_hash=hashlib.sha256(title.encode()).hexdigest(),
                content_hash=digest,
                content_type=content_classification.content_type,
                is_intelligence=content_classification.is_intelligence,
            )
            db.add(article)
            db.flush()
            haystack = (title + "\n" + summary).casefold()
            for topic in topics:
                hits = sorted(
                    {
                        term
                        for term in [topic.display_name, *topic.aliases, *topic.keywords]
                        if term and term.casefold() in haystack
                    }
                )
                if hits:
                    db.add(ArticleTopic(article_id=article.id, topic_id=topic.id, matched_keywords=hits))
            db.flush()
            db.refresh(article, attribute_names=["topic_links"])
            if article.is_intelligence:
                classified += classify_article(db, article)
            inserted += 1
    db.commit()
    return {
        "sources": len(sources),
        "failed_sources": failed_sources,
        "inserted": inserted,
        "duplicate": duplicate,
        "classified": classified,
    }

OFFICIAL_CONTENT_LIMIT = 20000


def load_official_pages() -> list[dict[str, str]]:
    settings = get_settings()
    if not settings.sources_config_path.exists():
        return []
    with settings.sources_config_path.open(encoding="utf-8") as file:
        configured = yaml.safe_load(file) or {}
    pages = []
    for item in configured.get("official_pages", []):
        if not item.get("enabled", True):
            continue
        pages.append({"name": item["name"], "url": item["url"], "topic": item["topic"]})
    return pages


def ingest_official_pages(db: Session) -> dict[str, int]:
    topics = sync_topics(db)
    topic_by_slug = {topic.slug: topic for topic in topics}
    pages = load_official_pages()
    inserted = duplicate = classified = failed_pages = failed_items = 0
    for page in pages:
        topic = topic_by_slug.get(page["topic"])
        if topic is None:
            failed_pages += 1
            continue
        source = get_or_create_source(
            db,
            {
                "name": page["name"],
                "feed_url": page["url"],
                "source_type": "official",
                "trust_tier": "verified",
            },
        )
        try:
            announcements = discover_announcements(page["url"])
        except OSError:
            failed_pages += 1
            continue
        for item in announcements:
            clean_url = canonical(item["url"])
            if clean_url == canonical(page["url"]):
                continue
            exists = db.scalar(
                select(Article.id).where(
                    (Article.original_url == item["url"]) | (Article.canonical_url == clean_url)
                )
            )
            if exists:
                duplicate += 1
                continue
            try:
                document = fetch_html(item["url"])
            except OSError:
                failed_items += 1
                continue
            text = html_to_text(document)[:OFFICIAL_CONTENT_LIMIT]
            title = item["title"] or extract_title(document)
            if not text or not title:
                failed_items += 1
                continue
            digest = hashlib.sha256((title + text).encode()).hexdigest()
            article = Article(
                source_id=source.id,
                title=title,
                summary=text[:500],
                content=text,
                original_url=item["url"],
                canonical_url=clean_url,
                source_name=source.name,
                source_domain=source.domain,
                published_at=None,
                title_hash=hashlib.sha256(title.encode()).hexdigest(),
                content_hash=digest,
                content_type="official_announcement",
                is_intelligence=True,
            )
            db.add(article)
            db.flush()
            db.add(
                ArticleTopic(
                    article_id=article.id, topic_id=topic.id, matched_keywords=["official_page"]
                )
            )
            db.flush()
            db.refresh(article, attribute_names=["topic_links"])
            classified += classify_article(db, article)
            inserted += 1
    db.commit()
    return {
        "pages": len(pages),
        "failed_pages": failed_pages,
        "failed_items": failed_items,
        "inserted": inserted,
        "duplicate": duplicate,
        "classified": classified,
    }


def load_bilibili_accounts() -> list[dict]:
    settings = get_settings()
    if not settings.sources_config_path.exists():
        return []
    with settings.sources_config_path.open(encoding="utf-8") as file:
        configured = yaml.safe_load(file) or {}
    accounts = []
    for item in configured.get("bilibili_accounts", []):
        if not item.get("enabled", True):
            continue
        accounts.append(
            {
                "name": item["name"],
                "mid": int(item["mid"]),
                "topic": item["topic"],
                "source_type": item.get("source_type", "official"),
                "trust_tier": item.get("trust_tier", "verified"),
            }
        )
    return accounts


def ingest_bilibili(db: Session) -> dict[str, int]:
    settings = get_settings()
    topics = sync_topics(db)
    topic_by_slug = {topic.slug: topic for topic in topics}
    accounts = load_bilibili_accounts()
    client = BilibiliClient(cookie=settings.bilibili_cookie)
    client.bootstrap()
    inserted = duplicate = classified = failed_accounts = failed_items = 0
    comments_stored = metrics_computed = 0
    for account in accounts:
        topic = topic_by_slug.get(account["topic"])
        if topic is None:
            failed_accounts += 1
            continue
        source = get_or_create_source(
            db,
            {
                "name": account["name"],
                "feed_url": f"https://space.bilibili.com/{account['mid']}/video",
                "source_type": account["source_type"],
                "trust_tier": account["trust_tier"],
            },
        )
        try:
            videos = client.account_videos(account["mid"], limit=settings.bilibili_videos_per_account)
        except (BilibiliError, OSError, ValueError):
            failed_accounts += 1
            continue
        for video in videos:
            url = f"https://www.bilibili.com/video/{video['bvid']}"
            clean_url = canonical(url)
            exists = db.scalar(
                select(Article.id).where(
                    (Article.original_url == url) | (Article.canonical_url == clean_url)
                )
            )
            if exists:
                duplicate += 1
                continue
            try:
                comments = client.video_comments(
                    video["aid"], limit=settings.bilibili_comments_per_video
                )
            except (BilibiliError, OSError, ValueError):
                failed_items += 1
                comments = []
            body = video["description"].strip()
            comments_section = format_comments_section(comments)
            text = f"{body}\n\n{comments_section}".strip()[:OFFICIAL_CONTENT_LIMIT]
            title = video["title"].strip()
            if not title:
                failed_items += 1
                continue
            digest = hashlib.sha256((title + text).encode()).hexdigest()
            article = Article(
                source_id=source.id,
                title=title,
                summary=(body or text)[:500],
                content=text,
                original_url=url,
                canonical_url=clean_url,
                source_name=source.name,
                source_domain=source.domain,
                published_at=datetime.fromtimestamp(video["pubdate"], UTC) if video["pubdate"] else None,
                title_hash=hashlib.sha256(title.encode()).hexdigest(),
                content_hash=digest,
                content_type="official_announcement",
                is_intelligence=True,
            )
            db.add(article)
            db.flush()
            db.add(
                ArticleTopic(
                    article_id=article.id, topic_id=topic.id, matched_keywords=["bilibili_official"]
                )
            )
            db.flush()
            db.refresh(article, attribute_names=["topic_links"])
            classified += classify_article(db, article)
            inserted += 1
        # Distortion detection: deep-crawl comments of the newest video per account.
        if videos and settings.bilibili_comment_pages > 0:
            newest = videos[0]
            newest_url = f"https://www.bilibili.com/video/{newest['bvid']}"
            newest_article = db.scalar(
                select(Article).where(
                    (Article.original_url == newest_url)
                    | (Article.canonical_url == canonical(newest_url))
                )
            )
            if newest_article is not None:
                try:
                    full = client.video_comments_full(
                        newest["aid"], max_pages=settings.bilibili_comment_pages
                    )
                except (BilibiliError, OSError, ValueError):
                    failed_items += 1
                    full = []
                seen_ids: set[str] = set()
                for comment in full:
                    if comment["comment_id"] in seen_ids:
                        continue
                    seen_ids.add(comment["comment_id"])
                    seen = db.scalar(
                        select(CommunityComment.id).where(
                            CommunityComment.platform == "bilibili",
                            CommunityComment.comment_id == comment["comment_id"],
                        )
                    )
                    if seen:
                        continue
                    db.add(
                        CommunityComment(
                            article_id=newest_article.id,
                            platform="bilibili",
                            comment_id=comment["comment_id"],
                            user_mid=comment["user_mid"],
                            message=comment["message"],
                            like_count=comment["like"],
                            published_at=(
                                datetime.fromtimestamp(comment["ctime"], UTC)
                                if comment["ctime"]
                                else None
                            ),
                        )
                    )
                    comments_stored += 1
                db.flush()
                rows = db.scalars(
                    select(CommunityComment).where(
                        CommunityComment.article_id == newest_article.id
                    )
                ).all()
                # Anonymous access may yield only a handful of hot comments;
                # too few comments make concentration metrics meaningless.
                if len(rows) >= 30:
                    newest_article.comment_metrics = compute_comment_metrics(
                        [
                            {
                                "user_mid": row.user_mid,
                                "message": row.message,
                                "like": row.like_count,
                                "ctime": (
                                    int(row.published_at.timestamp())
                                    if row.published_at
                                    else None
                                ),
                            }
                            for row in rows
                        ]
                    )
                    metrics_computed += 1
    db.commit()
    return {
        "accounts": len(accounts),
        "failed_accounts": failed_accounts,
        "failed_items": failed_items,
        "inserted": inserted,
        "duplicate": duplicate,
        "classified": classified,
        "comments_stored": comments_stored,
        "metrics_computed": metrics_computed,
    }


TIEBA_REPLIES_PER_THREAD = 30
TIEBA_HOT_REPLIES_IN_CONTENT = 10


def load_tieba_forums() -> list[dict]:
    settings = get_settings()
    if not settings.sources_config_path.exists():
        return []
    with settings.sources_config_path.open(encoding="utf-8") as file:
        configured = yaml.safe_load(file) or {}
    forums = []
    for item in configured.get("tieba_forums", []):
        if not item.get("enabled", True):
            continue
        forums.append(
            {
                "name": item["name"],
                "kw": item["kw"],
                "topic": item["topic"],
                "source_type": item.get("source_type", "community"),
                "trust_tier": item.get("trust_tier", "unverified"),
            }
        )
    return forums


def ingest_tieba(db: Session) -> dict[str, int]:
    settings = get_settings()
    topics = sync_topics(db)
    topic_by_slug = {topic.slug: topic for topic in topics}
    forums = load_tieba_forums()
    client = TiebaClient()
    inserted = duplicate = classified = failed_forums = failed_items = skipped_threads = 0
    comments_stored = metrics_computed = 0
    for forum in forums:
        topic = topic_by_slug.get(forum["topic"])
        if topic is None:
            failed_forums += 1
            continue
        source = get_or_create_source(
            db,
            {
                "name": forum["name"],
                "feed_url": f"https://tieba.baidu.com/f?kw={forum['kw']}",
                "source_type": forum["source_type"],
                "trust_tier": forum["trust_tier"],
            },
        )
        try:
            threads = client.forum_threads(
                forum["kw"], limit=settings.tieba_threads_per_forum
            )
        except (TiebaError, OSError, ValueError):
            failed_forums += 1
            continue
        for thread in threads:
            # Threads with almost no replies are drive-by posts, not discussion.
            if thread["reply_num"] < settings.tieba_min_replies:
                skipped_threads += 1
                continue
            url = f"https://tieba.baidu.com/p/{thread['tid']}"
            clean_url = canonical(url)
            exists = db.scalar(
                select(Article.id).where(
                    (Article.original_url == url) | (Article.canonical_url == clean_url)
                )
            )
            if exists:
                duplicate += 1
                continue
            try:
                posts = client.thread_posts(thread["tid"], limit=TIEBA_REPLIES_PER_THREAD)
            except (TiebaError, OSError, ValueError):
                failed_items += 1
                continue
            if not posts:
                failed_items += 1
                continue
            first = next((post for post in posts if post["floor"] == 1), posts[0])
            replies = [post for post in posts if post is not first]
            body = first["text"].strip()
            title = thread["title"].strip() or body[:50]
            if not title:
                failed_items += 1
                continue
            hot = sorted(replies, key=lambda post: post["like"], reverse=True)[
                :TIEBA_HOT_REPLIES_IN_CONTENT
            ]
            replies_section = format_replies_section(
                [{"message": post["text"], "like": post["like"]} for post in hot]
            )
            text = f"{body}\n\n{replies_section}".strip()[:OFFICIAL_CONTENT_LIMIT]
            created = thread["create_time"] or first["time"]
            digest = hashlib.sha256((title + text).encode()).hexdigest()
            article = Article(
                source_id=source.id,
                title=title,
                summary=(body or text)[:500],
                content=text,
                original_url=url,
                canonical_url=clean_url,
                source_name=source.name,
                source_domain=source.domain,
                published_at=datetime.fromtimestamp(created, UTC) if created else None,
                title_hash=hashlib.sha256(title.encode()).hexdigest(),
                content_hash=digest,
                content_type="community_post",
                is_intelligence=True,
            )
            db.add(article)
            db.flush()
            db.add(
                ArticleTopic(
                    article_id=article.id, topic_id=topic.id, matched_keywords=["tieba_forum"]
                )
            )
            db.flush()
            db.refresh(article, attribute_names=["topic_links"])
            classified += classify_article(db, article)
            inserted += 1
            # Distortion detection: store the first reply floors of every thread.
            seen_ids: set[str] = set()
            for index, reply in enumerate(replies[:TIEBA_REPLIES_PER_THREAD]):
                comment_id = reply["post_id"] or f"{thread['tid']}_{reply['floor'] or index + 2}"
                if comment_id in seen_ids:
                    continue
                seen_ids.add(comment_id)
                seen = db.scalar(
                    select(CommunityComment.id).where(
                        CommunityComment.platform == "tieba",
                        CommunityComment.comment_id == comment_id,
                    )
                )
                if seen:
                    continue
                db.add(
                    CommunityComment(
                        article_id=article.id,
                        platform="tieba",
                        comment_id=comment_id,
                        user_mid=reply["user_key"][:32],
                        message=reply["text"],
                        like_count=reply["like"],
                        published_at=(
                            datetime.fromtimestamp(reply["time"], UTC) if reply["time"] else None
                        ),
                    )
                )
                comments_stored += 1
            db.flush()
            rows = db.scalars(
                select(CommunityComment).where(CommunityComment.article_id == article.id)
            ).all()
            # Too few comments make concentration metrics meaningless.
            if len(rows) >= 30:
                article.comment_metrics = compute_comment_metrics(
                    [
                        {
                            "user_mid": row.user_mid,
                            "message": row.message,
                            "like": row.like_count,
                            "ctime": (
                                int(row.published_at.timestamp()) if row.published_at else None
                            ),
                        }
                        for row in rows
                    ]
                )
                metrics_computed += 1
    db.commit()
    return {
        "forums": len(forums),
        "failed_forums": failed_forums,
        "failed_items": failed_items,
        "skipped_threads": skipped_threads,
        "inserted": inserted,
        "duplicate": duplicate,
        "classified": classified,
        "comments_stored": comments_stored,
        "metrics_computed": metrics_computed,
    }


NGA_THREADS_PER_FORUM = 20
NGA_FLOORS_PER_THREAD = 20
WEIBO_POSTS_PER_QUERY = 20
MIYOUSHE_POSTS_PER_FORUM = 20
# getForumPostList keys boards by numeric gids; the public article URL uses the
# game's path prefix instead.
MIYOUSHE_GAME_PATH = {2: "ys", 6: "sr", 8: "zzz", 1: "bh3"}


def _load_community_section(section: str, fields: tuple[str, ...]) -> list[dict]:
    settings = get_settings()
    if not settings.sources_config_path.exists():
        return []
    with settings.sources_config_path.open(encoding="utf-8") as file:
        configured = yaml.safe_load(file) or {}
    entries = []
    for item in configured.get(section, []):
        if not item.get("enabled", True):
            continue
        entry = {field: item[field] for field in fields}
        entry["source_type"] = item.get("source_type", "community")
        entry["trust_tier"] = item.get("trust_tier", "unverified")
        entries.append(entry)
    return entries


def load_nga_forums() -> list[dict]:
    return _load_community_section("nga_forums", ("name", "topic", "fid"))


def load_weibo_queries() -> list[dict]:
    return _load_community_section("weibo_queries", ("name", "topic", "query"))


def load_miyoushe_forums() -> list[dict]:
    return _load_community_section("miyoushe_forums", ("name", "topic", "gids", "forum_id"))


def _store_article(
    db: Session,
    source: Source,
    topic: Topic,
    matched: str,
    title: str,
    body: str,
    url: str,
    created: int | None,
    published: datetime | None = None,
    canonical_url: str | None = None,
) -> tuple[Article, int]:
    """Shared community-post persistence: article + topic link + sentiment.

    `canonical_url` overrides the default query-stripped form for sites whose
    identity lives in the query string (NGA read.php?tid=...).
    """
    text = body.strip()[:OFFICIAL_CONTENT_LIMIT]
    title = title.strip() or body[:50]
    digest = hashlib.sha256((title + text).encode()).hexdigest()
    article = Article(
        source_id=source.id,
        title=title,
        summary=(body or text)[:500],
        content=text,
        original_url=url,
        canonical_url=canonical_url if canonical_url is not None else canonical(url),
        source_name=source.name,
        source_domain=source.domain,
        published_at=published if published is not None else (
            datetime.fromtimestamp(created, UTC) if created else None
        ),
        title_hash=hashlib.sha256(title.encode()).hexdigest(),
        content_hash=digest,
        content_type="community_post",
        is_intelligence=True,
    )
    db.add(article)
    db.flush()
    db.add(ArticleTopic(article_id=article.id, topic_id=topic.id, matched_keywords=[matched]))
    db.flush()
    db.refresh(article, attribute_names=["topic_links"])
    classified = classify_article(db, article)
    return article, classified


def _store_comments(
    db: Session, article: Article, platform: str, comments: list[dict]
) -> tuple[int, int]:
    """Dedup and store community_comments; compute metrics when enough rows."""
    stored = metrics = 0
    seen_ids: set[str] = set()
    for comment in comments:
        if comment["comment_id"] in seen_ids:
            continue
        seen_ids.add(comment["comment_id"])
        seen = db.scalar(
            select(CommunityComment.id).where(
                CommunityComment.platform == platform,
                CommunityComment.comment_id == comment["comment_id"],
            )
        )
        if seen:
            continue
        db.add(
            CommunityComment(
                article_id=article.id,
                platform=platform,
                comment_id=comment["comment_id"],
                user_mid=comment["user_mid"][:32],
                message=comment["message"],
                like_count=comment["like"],
                published_at=(
                    datetime.fromtimestamp(comment["ctime"], UTC) if comment["ctime"] else None
                ),
            )
        )
        stored += 1
    db.flush()
    rows = db.scalars(
        select(CommunityComment).where(CommunityComment.article_id == article.id)
    ).all()
    # Too few comments make concentration metrics meaningless.
    if len(rows) >= 30:
        article.comment_metrics = compute_comment_metrics(
            [
                {
                    "user_mid": row.user_mid,
                    "message": row.message,
                    "like": row.like_count,
                    "ctime": int(row.published_at.timestamp()) if row.published_at else None,
                }
                for row in rows
            ]
        )
        metrics = 1
    return stored, metrics


def ingest_nga(db: Session) -> dict[str, int]:
    topics = sync_topics(db)
    topic_by_slug = {topic.slug: topic for topic in topics}
    forums = load_nga_forums()
    stats = {
        "forums": len(forums),
        "failed_forums": 0,
        "failed_items": 0,
        "inserted": 0,
        "duplicate": 0,
        "classified": 0,
        "comments_stored": 0,
        "metrics_computed": 0,
    }
    cookie = load_cookie_header("nga")
    if cookie is None:
        logger.warning("NGA session missing or malformed; skipping nga ingestion lane")
        return stats
    client = NgaClient(cookie=cookie)
    for forum in forums:
        topic = topic_by_slug.get(forum["topic"])
        if topic is None:
            stats["failed_forums"] += 1
            continue
        source = get_or_create_source(
            db,
            {
                "name": forum["name"],
                "feed_url": f"https://bbs.nga.cn/thread.php?fid={forum['fid']}",
                "source_type": forum["source_type"],
                "trust_tier": forum["trust_tier"],
            },
        )
        try:
            threads = client.forum_threads(forum["fid"], limit=NGA_THREADS_PER_FORUM)
        except (NgaError, OSError, ValueError) as error:
            logger.warning("NGA forum %s failed: %s", forum["fid"], error)
            stats["failed_forums"] += 1
            continue
        for thread in threads:
            # The thread id lives in the query string, so the query-stripped
            # canonical form cannot deduplicate NGA threads; use the full URL.
            url = f"https://bbs.nga.cn/read.php?tid={thread['tid']}"
            exists = db.scalar(
                select(Article.id).where(
                    (Article.original_url == url) | (Article.canonical_url == url)
                )
            )
            if exists:
                stats["duplicate"] += 1
                continue
            try:
                posts = client.thread_posts(thread["tid"], limit=NGA_FLOORS_PER_THREAD)
            except (NgaError, OSError, ValueError) as error:
                # Some threads return truncated JSON from read.php; keep the
                # article (title + reply count) and skip its floors.
                logger.warning("NGA thread %s floors unavailable: %s", thread["tid"], error)
                stats["failed_items"] += 1
                posts = []
            opening = next((post for post in posts if post["floor"] == 0), None)
            replies = [post for post in posts if post is not opening]
            body = opening["text"] if opening else ""
            body = f"{body}\n\n回复 {thread['reply_num']}".strip()
            article, classified = _store_article(
                db, source, topic, "nga_forum",
                thread["title"] or body[:50], body, url, thread["create_time"],
                canonical_url=url,
            )
            stats["classified"] += classified
            stats["inserted"] += 1
            stored, metrics = _store_comments(
                db,
                article,
                "nga",
                [
                    {
                        "comment_id": reply["post_id"] or f"{thread['tid']}_{reply['floor']}",
                        "user_mid": reply["author"],
                        "message": reply["text"],
                        "like": 0,
                        "ctime": reply["time"],
                    }
                    for reply in replies[:NGA_FLOORS_PER_THREAD]
                ],
            )
            stats["comments_stored"] += stored
            stats["metrics_computed"] += metrics
    db.commit()
    return stats


def ingest_weibo(db: Session) -> dict[str, int]:
    topics = sync_topics(db)
    topic_by_slug = {topic.slug: topic for topic in topics}
    queries = load_weibo_queries()
    stats = {
        "queries": len(queries),
        "failed_queries": 0,
        "failed_items": 0,
        "inserted": 0,
        "duplicate": 0,
        "classified": 0,
    }
    cookie = load_cookie_header("weibo")
    if cookie is None:
        logger.warning("Weibo session missing or malformed; skipping weibo ingestion lane")
        return stats
    client = WeiboClient(cookie=cookie)
    for entry in queries:
        topic = topic_by_slug.get(entry["topic"])
        if topic is None:
            stats["failed_queries"] += 1
            continue
        source = get_or_create_source(
            db,
            {
                "name": entry["name"],
                "feed_url": f"https://s.weibo.com/weibo?q={entry['query']}",
                "source_type": entry["source_type"],
                "trust_tier": entry["trust_tier"],
            },
        )
        try:
            posts = client.search_posts(entry["query"], limit=WEIBO_POSTS_PER_QUERY)
        except WeiboError as error:
            logger.warning("Weibo query %r failed: %s", entry["query"], error)
            stats["failed_queries"] += 1
            continue
        for post in posts:
            url = post["url"]
            clean_url = canonical(url)
            exists = db.scalar(
                select(Article.id).where(
                    (Article.original_url == url) | (Article.canonical_url == clean_url)
                )
            )
            if exists:
                stats["duplicate"] += 1
                continue
            body = (
                f"{post['text']}\n\n"
                f"转发 {post['repost']} · 评论 {post['comment']} · 赞 {post['like']}"
            )
            try:
                _article, classified = _store_article(
                    db, source, topic, "weibo_search",
                    post["text"][:50], body, url, None,
                    published=post["published_at"],
                )
            except (ValueError, KeyError):
                stats["failed_items"] += 1
                continue
            stats["classified"] += classified
            stats["inserted"] += 1
    db.commit()
    return stats


def ingest_miyoushe(db: Session) -> dict[str, int]:
    topics = sync_topics(db)
    topic_by_slug = {topic.slug: topic for topic in topics}
    forums = load_miyoushe_forums()
    stats = {
        "forums": len(forums),
        "failed_forums": 0,
        "failed_items": 0,
        "inserted": 0,
        "duplicate": 0,
        "classified": 0,
    }
    client = MiyousheClient()
    for forum in forums:
        topic = topic_by_slug.get(forum["topic"])
        if topic is None:
            stats["failed_forums"] += 1
            continue
        gids = int(forum["gids"])
        forum_id = int(forum["forum_id"])
        game_path = MIYOUSHE_GAME_PATH.get(gids, "ys")
        source = get_or_create_source(
            db,
            {
                "name": forum["name"],
                "feed_url": (
                    f"https://bbs-api.miyoushe.com/post/wapi/getForumPostList"
                    f"?gids={gids}&forum_id={forum_id}"
                ),
                "source_type": forum["source_type"],
                "trust_tier": forum["trust_tier"],
            },
        )
        try:
            posts = client.forum_posts(gids, forum_id, limit=MIYOUSHE_POSTS_PER_FORUM)
        except MiyousheError as error:
            logger.warning("Miyoushe forum %s/%s failed: %s", gids, forum_id, error)
            stats["failed_forums"] += 1
            continue
        for post in posts:
            url = f"https://www.miyoushe.com/{game_path}/article/{post['post_id']}"
            clean_url = canonical(url)
            exists = db.scalar(
                select(Article.id).where(
                    (Article.original_url == url) | (Article.canonical_url == clean_url)
                )
            )
            if exists:
                stats["duplicate"] += 1
                continue
            body = (
                f"{post['text']}\n\n"
                f"回复 {post['reply_num']} · 赞 {post['like_num']} · 浏览 {post['view_num']}"
            ).strip()
            _article, classified = _store_article(
                db, source, topic, "miyoushe_forum",
                post["title"] or post["text"][:50], body, url, post["created_at"],
            )
            stats["classified"] += classified
            stats["inserted"] += 1
    db.commit()
    return stats
