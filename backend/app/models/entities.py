import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector
from app.db import Base


class Topic(Base):
    __tablename__ = "topics"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list[str]] = mapped_column(JSONB, default=list)
    keywords: Mapped[list[str]] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(default=True)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    domain: Mapped[str | None] = mapped_column(String(255))
    feed_url: Mapped[str] = mapped_column(String(2048), unique=True)


class Article(Base):
    __tablename__ = "articles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    title: Mapped[str] = mapped_column(String(1000))
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    original_url: Mapped[str] = mapped_column(String(2048), unique=True)
    canonical_url: Mapped[str | None] = mapped_column(String(2048), unique=True)
    source_name: Mapped[str] = mapped_column(String(200))
    source_domain: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    title_hash: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    processing_status: Mapped[str] = mapped_column(String(20), default="cleaned")
    search_vector: Mapped[object | None] = mapped_column(TSVECTOR, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    topic_links: Mapped[list["ArticleTopic"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )


class ArticleTopic(Base):
    __tablename__ = "article_topics"
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id"), primary_key=True)
    topic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topics.id"), primary_key=True)
    relevance_score: Mapped[float] = mapped_column(default=1.0)
    matched_keywords: Mapped[list[str]] = mapped_column(JSONB, default=list)
    article: Mapped[Article] = relationship(back_populates="topic_links")
    topic: Mapped[Topic] = relationship()


class ArticleSentiment(Base):
    __tablename__ = "article_sentiments"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id"))
    topic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topics.id"))
    label: Mapped[str] = mapped_column(String(16), default="neutral")
    score: Mapped[float] = mapped_column(default=0.0)
    confidence: Mapped[float] = mapped_column(default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="Pending classification")
    model_name: Mapped[str] = mapped_column(String(200), default="pending")
    __table_args__ = (UniqueConstraint("article_id", "topic_id", "model_name"),)


class ArticleChunk(Base):
    __tablename__ = "article_chunks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[object | None] = mapped_column(Vector(1536))
    __table_args__ = (UniqueConstraint("article_id", "chunk_index"),)
