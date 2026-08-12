"""add pg_trgm trigram index for chinese recall"""

from alembic import op

revision = "0005_pg_trgm_recall"
down_revision = "0004_community_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("""
        CREATE INDEX ix_articles_trgm_text
        ON articles USING gin ((title || ' ' || coalesce(content, '')) gin_trgm_ops)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_articles_trgm_text")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
