from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.collectors.nga import NgaClient, NgaError, clean_bbcode
from app.models.entities import Article, CommunityComment
from app.services import ingestion


def test_clean_bbcode_strips_tags_and_whitespace() -> None:
    assert clean_bbcode("[b] bold [/b][url=https://x.cn]link[/url]\n newline") == "bold link newline"
    assert clean_bbcode("") == ""
    assert clean_bbcode(None) == ""


def test_forum_threads_maps_fields(monkeypatch) -> None:
    client = NgaClient(cookie="fake", delay=0)
    captured: dict = {}
    payload = {
        "__T": {
            "0": {"tid": 123456, "subject": "新版本吐槽", "replies": 42,
                  "author": "楼主甲", "postdate": 1700000000},
            "1": {"tid": 123457, "subject": "晒卡", "replies": 3,
                  "author": "楼主乙", "postdate": 1700000100},
        },
        "__ROWS": 802501,
    }

    def fake_get(url: str) -> dict:
        captured["url"] = url
        return payload

    monkeypatch.setattr(client, "_get_json", fake_get)

    threads = client.forum_threads(-34587507, limit=20)

    assert captured["url"] == "https://bbs.nga.cn/thread.php?fid=-34587507&__output=8"
    assert threads == [
        {"tid": "123456", "title": "新版本吐槽", "reply_num": 42,
         "author": "楼主甲", "create_time": 1700000000},
        {"tid": "123457", "title": "晒卡", "reply_num": 3,
         "author": "楼主乙", "create_time": 1700000100},
    ]


def test_thread_posts_sorts_floors_and_cleans_content(monkeypatch) -> None:
    client = NgaClient(cookie="fake", delay=0)
    payload = {
        "__R": {
            "2": {"pid": 12, "lou": 2, "author": "乙", "content": "[quote]引[/quote]顶",
                  "postdatetimestamp": 1700000200},
            "0": {"pid": 10, "lou": 0, "author": "楼主", "content": "[b]一楼[/b] 正文",
                  "postdatetimestamp": 1700000000},
            "1": {"pid": 11, "lou": 1, "author": "甲", "content": "[img]x.png[/img]",
                  "postdatetimestamp": 1700000100},
        },
    }
    monkeypatch.setattr(client, "_get_json", lambda url: payload)

    posts = client.thread_posts("123456", limit=20)

    assert posts == [
        {"post_id": "10", "floor": 0, "author": "楼主", "text": "一楼 正文", "time": 1700000000},
        {"post_id": "12", "floor": 2, "author": "乙", "text": "引 顶", "time": 1700000200},
    ]


def test_get_json_raises_on_non_200(monkeypatch) -> None:
    client = NgaClient(cookie="fake", delay=0)

    class FakeResponse:
        status_code = 403

    monkeypatch.setattr(client._client, "get", lambda *a, **k: FakeResponse())

    try:
        client._get_json("https://bbs.nga.cn/thread.php?fid=650&__output=8")
        raised = False
    except NgaError as error:
        raised = True
        assert "403" in str(error)
    assert raised


def test_load_nga_forums_reads_enabled_entries(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / "sources.yaml"
    config.write_text(
        "nga_forums:\n"
        "  - name: NGA 明日方舟\n"
        "    fid: -34587507\n"
        "    topic: arknights\n"
        "  - name: 已禁用版\n"
        "    enabled: false\n"
        "    fid: 1\n"
        "    topic: arknights\n",
        encoding="utf-8",
    )
    settings = SimpleNamespace(sources_config_path=config)
    monkeypatch.setattr(ingestion, "get_settings", lambda: settings)

    assert ingestion.load_nga_forums() == [
        {
            "name": "NGA 明日方舟",
            "topic": "arknights",
            "fid": -34587507,
            "source_type": "community",
            "trust_tier": "unverified",
        }
    ]


class FakeNgaClient:
    def __init__(self, cookie: str):
        self.cookie = cookie

    def forum_threads(self, fid: int, limit: int = 20) -> list[dict]:
        assert fid == -34587507
        return [
            {"tid": "100", "title": "版本讨论", "reply_num": 5,
             "author": "楼主", "create_time": 1700000000},
        ]

    def thread_posts(self, tid: str, limit: int = 20) -> list[dict]:
        assert tid == "100"
        return [
            {"post_id": "900", "floor": 0, "author": "楼主", "text": "一楼正文", "time": 1700000000},
            {"post_id": "901", "floor": 1, "author": "用户甲", "text": "顶", "time": 1700000100},
        ]


def _configs(tmp_path: Path) -> SimpleNamespace:
    topics_config = tmp_path / "topics.yaml"
    topics_config.write_text(
        "topics:\n  - {slug: arknights, display_name: 明日方舟, aliases: [], keywords: []}\n",
        encoding="utf-8",
    )
    sources_config = tmp_path / "sources.yaml"
    sources_config.write_text(
        "nga_forums:\n  - {name: NGA 明日方舟, fid: -34587507, topic: arknights}\n",
        encoding="utf-8",
    )
    return SimpleNamespace(topics_config_path=topics_config, sources_config_path=sources_config)


def _mock_db(comment_rows: list) -> MagicMock:
    db = MagicMock()
    db.scalar.return_value = None
    db.scalars.return_value.all.return_value = comment_rows
    return db


def test_ingest_nga_inserts_article_and_comments(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ingestion, "get_settings", lambda: _configs(tmp_path))
    monkeypatch.setattr(ingestion, "load_cookie_header", lambda site: "fake-cookie")
    monkeypatch.setattr(ingestion, "NgaClient", FakeNgaClient)
    db = _mock_db([])

    result = ingestion.ingest_nga(db)

    assert result["forums"] == 1
    assert result["inserted"] == 1
    assert result["duplicate"] == 0
    assert result["comments_stored"] == 1
    assert result["metrics_computed"] == 0

    added = [call.args[0] for call in db.add.call_args_list]
    article = next(item for item in added if isinstance(item, Article))
    assert article.original_url == "https://bbs.nga.cn/read.php?tid=100"
    # NGA thread identity lives in the query string; canonical keeps it.
    assert article.canonical_url == "https://bbs.nga.cn/read.php?tid=100"
    assert article.content_type == "community_post"
    assert "一楼正文" in article.content
    assert "回复 5" in article.content
    comments = [item for item in added if isinstance(item, CommunityComment)]
    assert len(comments) == 1
    assert comments[0].platform == "nga"
    assert comments[0].comment_id == "901"


def test_ingest_nga_skips_duplicate_threads(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ingestion, "get_settings", lambda: _configs(tmp_path))
    monkeypatch.setattr(ingestion, "load_cookie_header", lambda site: "fake-cookie")
    monkeypatch.setattr(ingestion, "NgaClient", FakeNgaClient)
    db = _mock_db([])
    # sync_topics, source lookup (feed_url then name) miss; the article URL
    # lookup hits an existing row so the thread is counted as duplicate.
    db.scalar.side_effect = [None, None, None, 1]

    result = ingestion.ingest_nga(db)

    assert result["inserted"] == 0
    assert result["duplicate"] == 1
    assert result["comments_stored"] == 0


def test_ingest_nga_skips_lane_without_session(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ingestion, "get_settings", lambda: _configs(tmp_path))
    monkeypatch.setattr(ingestion, "load_cookie_header", lambda site: None)
    db = _mock_db([])

    result = ingestion.ingest_nga(db)

    assert result["forums"] == 1
    assert result["inserted"] == 0
    added = [call.args[0] for call in db.add.call_args_list]
    assert not any(isinstance(item, (Article, CommunityComment)) for item in added)
