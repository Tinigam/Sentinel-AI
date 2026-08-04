"""Run the configured RSS ingestion once from the command line."""

from app.db import SessionLocal
from app.services.ingestion import ingest_rss


def main() -> None:
    with SessionLocal() as session:
        print(ingest_rss(session))


if __name__ == "__main__":
    main()