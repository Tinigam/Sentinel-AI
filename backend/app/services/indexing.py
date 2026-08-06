import hashlib
import math
import re

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.entities import Article, ArticleChunk, ArticleTopic

EMBEDDING_DIMENSIONS = 1536
EMBEDDING_MODEL = "local-hash.v1"


def chunk_text(text: str, size: int = 800) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    return [normalized[index : index + size] for index in range(0, len(normalized), size)] or [""]


def embed(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{1,2}", text.casefold())
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
        vector[index] += 1.0 if digest[4] % 2 else -1.0
    length = math.sqrt(sum(value * value for value in vector))
    return [value / length for value in vector] if length else vector


def index_article(db: Session, article: Article) -> int:
    text = f"{article.title}\n{article.summary or ''}\n{article.content or ''}"
    db.execute(delete(ArticleChunk).where(ArticleChunk.article_id == article.id))
    chunks = chunk_text(text)
    for index, content in enumerate(chunks):
        db.add(
            ArticleChunk(
                article_id=article.id,
                chunk_index=index,
                content=content,
                token_count=len(content),
                embedding=embed(content),
            )
        )
    article.processing_status = "indexed"
    return len(chunks)


def index_relevant_articles(db: Session) -> dict[str, int | str]:
    db.execute(
        delete(ArticleChunk).where(
            ArticleChunk.article_id.in_(select(Article.id).where(Article.is_intelligence.is_(False)))
        )
    )
    articles = db.scalars(
        select(Article).join(ArticleTopic).where(Article.is_intelligence.is_(True)).distinct()
    ).all()
    chunk_count = sum(index_article(db, article) for article in articles)
    db.commit()
    return {"articles": len(articles), "chunks": chunk_count, "embedding_model": EMBEDDING_MODEL}
