"""add source provenance and article content classification"""

from alembic import op
import sqlalchemy as sa

revision = "0003_content_quality"
down_revision = "0002_search_and_vector_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("source_type", sa.String(length=32), server_default="aggregator", nullable=False),
    )
    op.add_column(
        "sources",
        sa.Column("trust_tier", sa.String(length=32), server_default="aggregated", nullable=False),
    )
    op.add_column(
        "articles",
        sa.Column("content_type", sa.String(length=32), server_default="media_news", nullable=False),
    )
    op.add_column(
        "articles",
        sa.Column("is_intelligence", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_index("ix_articles_content_type", "articles", ["content_type"])
    op.create_index("ix_articles_is_intelligence", "articles", ["is_intelligence"])


def downgrade() -> None:
    op.drop_index("ix_articles_is_intelligence", table_name="articles")
    op.drop_index("ix_articles_content_type", table_name="articles")
    op.drop_column("articles", "is_intelligence")
    op.drop_column("articles", "content_type")
    op.drop_column("sources", "trust_tier")
    op.drop_column("sources", "source_type")