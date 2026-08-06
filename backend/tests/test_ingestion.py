from pathlib import Path
from types import SimpleNamespace

from app.services import ingestion


def test_canonical_removes_tracking_parts_and_normalizes_host() -> None:
    assert ingestion.canonical("HTTPS://Example.COM/path/?utm_source=test#section") == "https://example.com/path"


def test_published_prefers_published_timestamp() -> None:
    entry = SimpleNamespace(published_parsed=(2026, 8, 6, 12, 30, 0, 0, 0, 0))
    value = ingestion.published(entry)
    assert value is not None
    assert value.isoformat() == "2026-08-06T12:30:00+00:00"


def test_google_news_url_encodes_query() -> None:
    url = ingestion.google_news_feed_url('"原神" site:gamelook.com.cn')
    assert url.startswith("https://news.google.com/rss/search?")
    assert "ceid=CN%3Azh-Hans" in url
    assert "%E5%8E%9F%E7%A5%9E" in url


def test_load_rss_sources_uses_enabled_entries(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / "sources.yaml"
    config.write_text(
        "rss_sources:\n"
        "  - name: Direct feed\n"
        "    feed_url: https://example.com/feed.xml\n"
        "  - name: Disabled\n"
        "    enabled: false\n"
        "    query: disabled\n"
        "  - name: Search feed\n"
        "    query: test query\n",
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        sources_config_path=config,
        rss_source_name="Fallback",
        rss_feed_url="https://example.com/fallback.xml",
    )
    monkeypatch.setattr(ingestion, "get_settings", lambda: settings)

    assert ingestion.load_rss_sources() == [
        {"name": "Direct feed", "feed_url": "https://example.com/feed.xml"},
        {"name": "Search feed", "feed_url": ingestion.google_news_feed_url("test query")},
    ]