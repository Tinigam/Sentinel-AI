from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.db import get_db
from app.models.entities import Article, ArticleSentiment, ArticleTopic, Topic
from app.services.indexing import index_relevant_articles
from app.services.ingestion import ingest_rss
from app.services.retrieval import hybrid_search
from app.schemas import AskRequest
from app.services.rag import answer_question
from app.services.sentiment import MODEL_NAME, classify_unprocessed

router = APIRouter()


def require_ingest_key(x_ingest_key: str | None) -> None:
    settings = get_settings()
    if settings.app_env != "development" and x_ingest_key != settings.ingest_api_key:
        raise HTTPException(401, "Invalid ingest key")


def output(article: Article, db: Session) -> dict:
    sentiment_rows = db.execute(
        select(ArticleSentiment, Topic.slug)
        .join(Topic, Topic.id == ArticleSentiment.topic_id)
        .where(ArticleSentiment.article_id == article.id, ArticleSentiment.model_name == MODEL_NAME)
    ).all()
    return {
        "id": str(article.id),
        "title": article.title,
        "summary": article.summary,
        "source_name": article.source_name,
        "source_domain": article.source_domain,
        "original_url": article.original_url,
        "published_at": article.published_at,
        "topics": [
            {"slug": link.topic.slug, "display_name": link.topic.display_name}
            for link in article.topic_links
        ],
        "sentiments": [
            {
                "topic_slug": slug,
                "label": item.label,
                "confidence": item.confidence,
                "reason": item.reason,
            }
            for item, slug in sentiment_rows
        ],
    }


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(select(1))
    return {"status": "ok", "database": "ok", "version": "0.1.0"}


@router.get("/topics")
def topics(db: Session = Depends(get_db)) -> list[dict[str, str]]:
    return [
        {"slug": item.slug, "display_name": item.display_name}
        for item in db.scalars(select(Topic).where(Topic.is_active.is_(True))).all()
    ]


@router.get("/news")
def news(
    topic: str | None = None,
    sentiment: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    statement = (
        select(Article)
        .join(ArticleTopic)
        .options(selectinload(Article.topic_links).selectinload(ArticleTopic.topic))
    )
    count = select(func.count(func.distinct(Article.id))).join(ArticleTopic)
    if topic:
        topic_id = select(Topic.id).where(Topic.slug == topic).scalar_subquery()
        statement = statement.where(ArticleTopic.topic_id == topic_id)
        count = count.where(ArticleTopic.topic_id == topic_id)
    if sentiment:
        statement = statement.join(ArticleSentiment).where(
            ArticleSentiment.label == sentiment, ArticleSentiment.model_name == MODEL_NAME
        )
        count = count.join(ArticleSentiment).where(
            ArticleSentiment.label == sentiment, ArticleSentiment.model_name == MODEL_NAME
        )
    if date_from:
        statement = statement.where(Article.published_at >= date_from)
        count = count.where(Article.published_at >= date_from)
    if date_to:
        statement = statement.where(Article.published_at <= date_to)
        count = count.where(Article.published_at <= date_to)
    total = db.scalar(count) or 0
    rows = (
        db.scalars(
            statement.order_by(Article.published_at.desc().nullslast())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .unique()
        .all()
    )
    return {
        "items": [output(row, db) for row in rows],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@router.get("/news/{article_id}")
def one(article_id: UUID, db: Session = Depends(get_db)) -> dict:
    row = db.scalar(
        select(Article)
        .options(selectinload(Article.topic_links).selectinload(ArticleTopic.topic))
        .where(Article.id == article_id)
    )
    if row is None:
        raise HTTPException(404, "Article not found")
    return output(row, db)


@router.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db)) -> dict:
    total = db.scalar(select(func.count(func.distinct(Article.id))).join(ArticleTopic)) or 0
    labels = dict(
        db.execute(
            select(ArticleSentiment.label, func.count(ArticleSentiment.id))
            .where(ArticleSentiment.model_name == MODEL_NAME)
            .group_by(ArticleSentiment.label)
        ).all()
    )
    ranked = db.execute(
        select(Topic.slug, Topic.display_name, func.count(ArticleTopic.article_id))
        .join(ArticleTopic)
        .group_by(Topic.id)
        .order_by(func.count(ArticleTopic.article_id).desc())
        .limit(5)
    ).all()
    return {
        "total_articles": total,
        "sentiment": {
            "positive": labels.get("positive", 0),
            "neutral": labels.get("neutral", 0),
            "negative": labels.get("negative", 0),
        },
        "popular_topics": [
            {"slug": slug, "display_name": name, "article_count": count}
            for slug, name, count in ranked
        ],
    }


@router.get("/dashboard/trends")
def dashboard_trends(db: Session = Depends(get_db)) -> dict:
    day = func.date_trunc("day", Article.published_at).label("date")
    negative = (
        func.count(ArticleSentiment.id)
        .filter(ArticleSentiment.label == "negative")
        .label("negative_count")
    )
    rows = db.execute(
        select(day, func.count(func.distinct(Article.id)).label("article_count"), negative)
        .join(ArticleTopic)
        .outerjoin(
            ArticleSentiment,
            (ArticleSentiment.article_id == Article.id)
            & (ArticleSentiment.model_name == MODEL_NAME),
        )
        .where(Article.published_at.is_not(None))
        .group_by(day)
        .order_by(day)
    ).all()
    return {
        "granularity": "day",
        "series": [
            {
                "date": value.date().isoformat(),
                "article_count": count,
                "negative_count": negative_count,
            }
            for value, count, negative_count in rows
        ],
    }


@router.get("/search")
def search(
    query: str = Query(min_length=2, max_length=500),
    topic: str | None = None,
    sentiment: str | None = None,
    limit: int = Query(8, ge=1, le=20),
    db: Session = Depends(get_db),
) -> dict:
    return hybrid_search(db, query=query, topic=topic, sentiment=sentiment, limit=limit)


@router.post("/index", status_code=202)
def index(x_ingest_key: str | None = Header(default=None), db: Session = Depends(get_db)) -> dict:
    require_ingest_key(x_ingest_key)
    return {"status": "completed", **index_relevant_articles(db)}


@router.post("/ingest", status_code=202)
def ingest(x_ingest_key: str | None = Header(default=None), db: Session = Depends(get_db)) -> dict:
    require_ingest_key(x_ingest_key)
    return {"status": "completed", **ingest_rss(db)}


@router.post("/classify", status_code=202)
def classify(
    x_ingest_key: str | None = Header(default=None), db: Session = Depends(get_db)
) -> dict:
    require_ingest_key(x_ingest_key)
    return {"status": "completed", "classified": classify_unprocessed(db)}


@router.post("/ask")
def ask(request: AskRequest, db: Session = Depends(get_db)) -> dict:
    return answer_question(
        db,
        question=request.question,
        topic=request.topic,
        sentiment=request.sentiment,
    )
