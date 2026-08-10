import http.client
import json
import time
import urllib.error
import urllib.request

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Article, ArticleChunk, ArticleSentiment, ArticleTopic, Topic
from app.services.indexing import embed
from app.services.sentiment import sentiment_model_name

RRF_K = 60
# RRF shortlist size fed into the cross-encoder reranker (3x typical top-k).
RERANK_SHORTLIST = 30
RERANK_DOC_LIMIT = 500
RECALL_PER_LANE = 25


class RerankError(Exception):
    """Raised when the rerank API rejects or garbles a request."""


def _rerank(query: str, documents: list[str]) -> list[float]:
    """Cross-encoder relevance scores for query/document pairs (DashScope
    gte-rerank style API). Retries transient failures; raises RerankError."""
    settings = get_settings()
    request = urllib.request.Request(
        settings.rerank_base_url,
        data=json.dumps(
            {
                "model": settings.rerank_model,
                "input": {
                    "query": query,
                    "documents": [document[:RERANK_DOC_LIMIT] for document in documents],
                },
                "parameters": {"return_documents": False, "top_n": len(documents)},
            }
        ).encode(),
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
    )
    results: list | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
            results = payload["output"]["results"]
            break
        except urllib.error.HTTPError as error:
            if error.code < 500 and error.code != 429:
                raise RerankError(f"HTTP {error.code} from rerank API") from error
        except (OSError, http.client.HTTPException, ValueError, KeyError):
            pass  # transient network/truncation failure, retry below
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    if results is None:
        raise RerankError("Rerank API request failed after 3 attempts")
    scores = [0.0] * len(documents)
    for item in results:
        scores[int(item["index"])] = float(item["relevance_score"])
    return scores


def _recall(
    db: Session, query: str, topic: str | None, sentiment: str | None, model_name: str
) -> tuple[list, list]:
    fts = select(Article.id).where(
        Article.search_vector.op("@@")(func.websearch_to_tsquery("simple", query))
    )
    vector = select(
        ArticleChunk.article_id,
        ArticleChunk.content,
        ArticleChunk.embedding.cosine_distance(embed(query)).label("distance"),
    ).join(Article).where(Article.is_intelligence.is_(True))
    if topic:
        topic_id = select(Topic.id).where(Topic.slug == topic).scalar_subquery()
        fts = fts.join(ArticleTopic).where(ArticleTopic.topic_id == topic_id)
        vector = vector.join(ArticleTopic).where(ArticleTopic.topic_id == topic_id)
    if sentiment:
        fts = fts.join(ArticleSentiment).where(
            ArticleSentiment.label == sentiment, ArticleSentiment.model_name == model_name
        )
        vector = vector.join(ArticleSentiment).where(
            ArticleSentiment.label == sentiment, ArticleSentiment.model_name == model_name
        )
    fts_ids = [row[0] for row in db.execute(fts.limit(RECALL_PER_LANE)).all()]
    vector_rows = db.execute(vector.order_by("distance").limit(RECALL_PER_LANE)).all()
    return fts_ids, vector_rows


def hybrid_search(
    db: Session, query: str, topic: str | None = None, sentiment: str | None = None, limit: int = 8
) -> dict:
    model_name = sentiment_model_name()
    scores: dict[object, float] = {}
    snippets: dict[object, str] = {}
    fts_ids, vector_rows = _recall(db, query, topic, sentiment, model_name)
    candidate_count = len(fts_ids) + len(vector_rows)
    for rank, article_id in enumerate(fts_ids, start=1):
        scores[article_id] = scores.get(article_id, 0) + 1 / (RRF_K + rank)
    for rank, (article_id, content, _) in enumerate(vector_rows, start=1):
        scores[article_id] = scores.get(article_id, 0) + 1 / (RRF_K + rank)
        snippets.setdefault(article_id, content[:360])
    ordered = sorted(scores, key=scores.get, reverse=True)
    shortlist = ordered[:RERANK_SHORTLIST]
    articles = {
        article.id: article
        for article in db.scalars(select(Article).where(Article.id.in_(shortlist))).all()
    }
    documents = {
        article_id: (
            f"{articles[article_id].title}\n"
            f"{(articles[article_id].content or articles[article_id].summary or '')[:400]}"
        )
        for article_id in shortlist
        if article_id in articles
    }
    method = "hybrid_rrf"
    settings = get_settings()
    if settings.rerank_model and settings.openai_api_key and documents:
        try:
            ids = list(documents)
            rerank_scores = _rerank(query, [documents[article_id] for article_id in ids])
            scores.update(dict(zip(ids, rerank_scores, strict=True)))
            ordered = sorted(ids, key=scores.get, reverse=True)
            method += "+rerank"
        except RerankError:
            pass  # keep RRF order; retrieval must stay available
    ordered = ordered[:limit]
    return {
        "method": method,
        "candidate_count": candidate_count,
        "results": [
            {
                "article_id": str(article_id),
                "score": round(scores[article_id], 6),
                "title": articles[article_id].title,
                "content_type": articles[article_id].content_type,
                "source": articles[article_id].source_name,
                "published_at": articles[article_id].published_at,
                "url": articles[article_id].original_url,
                "snippet": snippets.get(article_id, (articles[article_id].summary or "")[:360]),
            }
            for article_id in ordered
            if article_id in articles
        ],
    }
