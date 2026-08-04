import hashlib
import html
import re
from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse

import feedparser
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Article, ArticleTopic, Source, Topic
from app.services.sentiment import classify_article


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


def ingest_rss(db: Session) -> dict[str, int]:
    settings = get_settings()
    source = db.scalar(select(Source).where(Source.feed_url == settings.rss_feed_url))
    if source is None:
        source = Source(
            name=settings.rss_source_name,
            domain=urlparse(settings.rss_feed_url).netloc,
            feed_url=settings.rss_feed_url,
        )
        db.add(source)
        db.flush()
    topics = sync_topics(db)
    feed = feedparser.parse(settings.rss_feed_url)
    if feed.bozo and not feed.entries:
        raise RuntimeError(f"RSS parsing failed: {feed.bozo_exception}")
    inserted = duplicate = classified = 0
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
                db.add(
                    ArticleTopic(article_id=article.id, topic_id=topic.id, matched_keywords=hits)
                )
        db.flush()
        db.refresh(article, attribute_names=["topic_links"])
        classified += classify_article(db, article)
        inserted += 1
    db.commit()
    return {"inserted": inserted, "duplicate": duplicate, "classified": classified}
