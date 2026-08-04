from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import Article, ArticleChunk, ArticleSentiment, ArticleTopic, Topic
from app.services.indexing import embed
from app.services.sentiment import MODEL_NAME

RRF_K = 60


def hybrid_search(
    db: Session, query: str, topic: str | None = None, sentiment: str | None = None, limit: int = 8
) -> dict:
    fts = select(Article.id).where(
        Article.search_vector.op("@@")(func.websearch_to_tsquery("simple", query))
    )
    vector = select(
        ArticleChunk.article_id,
        ArticleChunk.content,
        ArticleChunk.embedding.cosine_distance(embed(query)).label("distance"),
    ).join(Article)
    if topic:
        topic_id = select(Topic.id).where(Topic.slug == topic).scalar_subquery()
        fts = fts.join(ArticleTopic).where(ArticleTopic.topic_id == topic_id)
        vector = vector.join(ArticleTopic).where(ArticleTopic.topic_id == topic_id)
    if sentiment:
        fts = fts.join(ArticleSentiment).where(
            ArticleSentiment.label == sentiment, ArticleSentiment.model_name == MODEL_NAME
        )
        vector = vector.join(ArticleSentiment).where(
            ArticleSentiment.label == sentiment, ArticleSentiment.model_name == MODEL_NAME
        )
    fts_ids = [row[0] for row in db.execute(fts.limit(20)).all()]
    vector_rows = db.execute(vector.order_by("distance").limit(20)).all()
    scores: dict[object, float] = {}
    snippets: dict[object, str] = {}
    for rank, article_id in enumerate(fts_ids, start=1):
        scores[article_id] = scores.get(article_id, 0) + 1 / (RRF_K + rank)
    for rank, (article_id, content, _) in enumerate(vector_rows, start=1):
        scores[article_id] = scores.get(article_id, 0) + 1 / (RRF_K + rank)
        snippets.setdefault(article_id, content[:360])
    ordered = sorted(scores, key=scores.get, reverse=True)[:limit]
    articles = {
        article.id: article
        for article in db.scalars(select(Article).where(Article.id.in_(ordered))).all()
    }
    return {
        "method": "hybrid_rrf",
        "candidate_count": len(fts_ids) + len(vector_rows),
        "results": [
            {
                "article_id": str(article_id),
                "score": round(scores[article_id], 6),
                "title": articles[article_id].title,
                "source": articles[article_id].source_name,
                "published_at": articles[article_id].published_at,
                "url": articles[article_id].original_url,
                "snippet": snippets.get(article_id, (articles[article_id].summary or "")[:360]),
            }
            for article_id in ordered
            if article_id in articles
        ],
    }
