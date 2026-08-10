"""add community comments and article comment metrics"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_community_comments"
down_revision = "0003_content_quality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("comment_metrics", JSONB, nullable=True))
    op.create_table(
        "community_comments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("article_id", sa.UUID(), sa.ForeignKey("articles.id"), nullable=False),
        sa.Column("platform", sa.String(length=32), server_default="bilibili", nullable=False),
        sa.Column("comment_id", sa.String(length=64), nullable=False),
        sa.Column("user_mid", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("like_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("platform", "comment_id"),
    )
    op.create_index("ix_community_comments_article_id", "community_comments", ["article_id"])
    op.create_index("ix_community_comments_user_mid", "community_comments", ["user_mid"])


def downgrade() -> None:
    op.drop_index("ix_community_comments_user_mid", table_name="community_comments")
    op.drop_index("ix_community_comments_article_id", table_name="community_comments")
    op.drop_table("community_comments")
    op.drop_column("articles", "comment_metrics")
