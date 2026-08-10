import hashlib
import http.client
import json
import math
import re
import time
import urllib.error
import urllib.request

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Article, ArticleChunk, ArticleTopic

EMBEDDING_DIMENSIONS = 1536
LOCAL_EMBEDDING_MODEL = "local-hash.v1"
# DashScope text-embedding-v4 accepts at most 10 inputs per call.
EMBED_BATCH_SIZE = 10


class EmbeddingError(Exception):
    """Raised when the remote embedding API rejects a request."""


def chunk_text(text: str, size: int = 800) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    return [normalized[index : index + size] for index in range(0, len(normalized), size)] or [""]


def embedding_model_name() -> str:
    settings = get_settings()
    return settings.embedding_model if settings.openai_api_key else LOCAL_EMBEDDING_MODEL


def _embed_local(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{1,2}", text.casefold())
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
        vector[index] += 1.0 if digest[4] % 2 else -1.0
    length = math.sqrt(sum(value * value for value in vector))
    return [value / length for value in vector] if length else vector


def _embed_remote(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    request = urllib.request.Request(
        f"{settings.llm_base_url}/embeddings",
        data=json.dumps(
            {
                "model": settings.embedding_model,
                "input": texts,
                "dimension": EMBEDDING_DIMENSIONS,
            }
        ).encode(),
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
    )
    payload: dict | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as error:
            if error.code < 500 and error.code != 429:
                raise EmbeddingError(f"HTTP {error.code} from embedding API") from error
        except (OSError, http.client.HTTPException, ValueError):
            pass  # transient network/truncation failure, retry below
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    if payload is None:
        raise EmbeddingError("Embedding API request failed after 3 attempts")
    data = payload.get("data") or []
    if len(data) != len(texts):
        raise EmbeddingError(f"Expected {len(texts)} vectors, got {len(data)}")
    return [item["embedding"] for item in data]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts with the configured provider. Provider is chosen once per
    process by API-key presence so indexed chunks and queries stay in the same
    vector space; remote failures raise instead of silently mixing spaces."""
    if not texts:
        return []
    if not get_settings().openai_api_key:
        return [_embed_local(text) for text in texts]
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        vectors.extend(_embed_remote(texts[start : start + EMBED_BATCH_SIZE]))
    return vectors


def embed(text: str) -> list[float]:
    return embed_texts([text])[0]


def index_relevant_articles(db: Session) -> dict[str, int | str]:
    db.execute(
        delete(ArticleChunk).where(
            ArticleChunk.article_id.in_(select(Article.id).where(Article.is_intelligence.is_(False)))
        )
    )
    articles = db.scalars(
        select(Article).join(ArticleTopic).where(Article.is_intelligence.is_(True)).distinct()
    ).all()
    prepared: list[tuple[Article, list[str]]] = []
    for article in articles:
        db.execute(delete(ArticleChunk).where(ArticleChunk.article_id == article.id))
        text = f"{article.title}\n{article.summary or ''}\n{article.content or ''}"
        prepared.append((article, chunk_text(text)))
    # Embed all chunks across articles in shared batches: one API call per
    # EMBED_BATCH_SIZE chunks instead of one per article.
    all_chunks = [chunk for _, chunks in prepared for chunk in chunks]
    vectors = embed_texts(all_chunks)
    offset = 0
    for article, chunks in prepared:
        for index, (content, vector) in enumerate(
            zip(chunks, vectors[offset : offset + len(chunks)], strict=True)
        ):
            db.add(
                ArticleChunk(
                    article_id=article.id,
                    chunk_index=index,
                    content=content,
                    token_count=len(content),
                    embedding=vector,
                )
            )
        offset += len(chunks)
        article.processing_status = "indexed"
    db.commit()
    return {
        "articles": len(articles),
        "chunks": len(all_chunks),
        "embedding_model": embedding_model_name(),
    }
