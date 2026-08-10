import hashlib
import html
import re
from datetime import UTC, datetime
from urllib.parse import urlencode, urlparse, urlunparse

import feedparser
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.collectors.official_pages import discover_announcements, extract_title, fetch_html, html_to_text
from app.models.entities import Article, ArticleTopic, Source, Topic
from app.services.content import classify_content_type
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
        except Exception:
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
            except Exception:
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
