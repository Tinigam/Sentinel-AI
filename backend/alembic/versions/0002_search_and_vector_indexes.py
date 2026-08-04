"""add full text and vector indexes"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_search_and_vector_indexes"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True))
    op.execute("""
        CREATE FUNCTION articles_search_vector_update() RETURNS trigger AS $$
        BEGIN
          NEW.search_vector := to_tsvector('simple',
            coalesce(NEW.title, '') || ' ' || coalesce(NEW.summary, '') || ' ' || coalesce(NEW.content, '')
          );
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER articles_search_vector_trigger
        BEFORE INSERT OR UPDATE OF title, summary, content ON articles
        FOR EACH ROW EXECUTE FUNCTION articles_search_vector_update();
    """)
    op.execute("UPDATE articles SET title = title")
    op.create_index(
        "ix_articles_search_vector", "articles", ["search_vector"], postgresql_using="gin"
    )
    op.execute("""
        CREATE INDEX ix_article_chunks_embedding_hnsw
        ON article_chunks USING hnsw (embedding vector_cosine_ops)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_article_chunks_embedding_hnsw")
    op.drop_index("ix_articles_search_vector", table_name="articles")
    op.execute("DROP TRIGGER IF EXISTS articles_search_vector_trigger ON articles")
    op.execute("DROP FUNCTION IF EXISTS articles_search_vector_update()")
    op.drop_column("articles", "search_vector")
