"""initial schema"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "topics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(100), unique=True, nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("aliases", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("keywords", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), unique=True, nullable=False),
        sa.Column("domain", sa.String(255)),
        sa.Column("feed_url", sa.String(2048), unique=True, nullable=False),
    )
    op.create_table(
        "articles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=False
        ),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("content", sa.Text()),
        sa.Column("original_url", sa.String(2048), unique=True, nullable=False),
        sa.Column("canonical_url", sa.String(2048), unique=True),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("source_domain", sa.String(255)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("title_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("processing_status", sa.String(20), server_default="cleaned", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_articles_published_at", "articles", ["published_at"])
    op.create_table(
        "article_topics",
        sa.Column(
            "article_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("articles.id"),
            primary_key=True,
        ),
        sa.Column(
            "topic_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("topics.id"), primary_key=True
        ),
        sa.Column("relevance_score", sa.Float(), server_default="1", nullable=False),
        sa.Column("matched_keywords", postgresql.JSONB(), server_default="[]", nullable=False),
    )
    op.create_table(
        "article_sentiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "article_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("articles.id"),
            nullable=False,
        ),
        sa.Column(
            "topic_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("topics.id"), nullable=False
        ),
        sa.Column("label", sa.String(16), server_default="neutral", nullable=False),
        sa.Column("score", sa.Float(), server_default="0", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("reason", sa.Text(), server_default="Pending classification", nullable=False),
        sa.Column("model_name", sa.String(200), server_default="pending", nullable=False),
        sa.UniqueConstraint("article_id", "topic_id", "model_name"),
    )
    op.create_table(
        "article_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "article_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("articles.id"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1536)),
        sa.UniqueConstraint("article_id", "chunk_index"),
    )


def downgrade():
    op.drop_table("article_chunks")
    op.drop_table("article_sentiments")
    op.drop_table("article_topics")
    op.drop_table("articles")
    op.drop_table("sources")
    op.drop_table("topics")
