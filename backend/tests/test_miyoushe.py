from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.collectors.miyoushe import MiyousheClient, MiyousheError, post_text
from app.models.entities import Article
from app.services import ingestion


def test_post_text_extracts_describe() -> None:
    assert post_text('{"describe": "12-1 都打不过", "imgs": ["x.png"]}') == "12-1 都打不过"
    assert post_text("plain text") == "plain text"
    assert post_text("") == ""
    assert post_text(None) == ""


def test_forum_posts_maps_fields(monkeypatch) -> None:
    client = MiyousheClient(delay=0)
    captured: dict = {}
    payload = {
        "retcode": 0,
        "data": {
            "list": [
                {
                    "post": {"post_id": "77385104", "subject": "求配队",
                             "content": '{"describe": "12-1 打不过"}',
                             "created_at": 1754900000, "uid": "1001"},
                    "stat": {"reply_num": 2, "like_num": 1, "view_num": 11},
                    "forum": {"name": "酒馆"},
                },
                {"post": {"subject": "无 post_id 跳过"}},
            ]
        },
    }

    class FakeResponse:
        def json(self):
            return payload

    def fake_get(url: str, timeout: int):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr(client._client, "get", fake_get)

    posts = client.forum_posts(2, 26, limit=20)

    assert "gids=2" in captured["url"] and "forum_id=26" in captured["url"]
    assert posts == [
        {
            "post_id": "77385104",
            "title": "求配队",
            "text": "12-1 打不过",
            "uid": "1001",
            "created_at": 1754900000,
            "reply_num": 2,
            "like_num": 1,
            "view_num": 11,
        }
    ]


def test_forum_posts_raises_on_bad_retcode(monkeypatch) -> None:
    client = MiyousheClient(delay=0)

    class FakeResponse:
        def json(self):
            return {"retcode": -1, "data": {}}

    monkeypatch.setattr(client._client, "get", lambda *a, **k: FakeResponse())

    try:
        client.forum_posts(2, 26)
        raised = False
    except MiyousheError as error:
        raised = True
        assert "-1" in str(error)
    assert raised


class FakeMiyousheClient:
    def forum_posts(self, gids: int, forum_id: int, limit: int = 20) -> list[dict]:
        assert (gids, forum_id) == (2, 26)
        return [
            {"post_id": "77385104", "title": "求配队", "text": "12-1 打不过", "uid": "1001",
             "created_at": 1754900000, "reply_num": 2, "like_num": 1, "view_num": 11},
        ]


def _configs(tmp_path: Path) -> SimpleNamespace:
    topics_config = tmp_path / "topics.yaml"
    topics_config.write_text(
        "topics:\n  - {slug: genshin-impact, display_name: 原神, aliases: [], keywords: []}\n",
        encoding="utf-8",
    )
    sources_config = tmp_path / "sources.yaml"
    sources_config.write_text(
        "miyoushe_forums:\n  - {name: 米游社 原神酒馆, gids: 2, forum_id: 26, topic: genshin-impact}\n",
        encoding="utf-8",
    )
    return SimpleNamespace(topics_config_path=topics_config, sources_config_path=sources_config)


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.scalar.return_value = None
    return db


def test_ingest_miyoushe_inserts_articles(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ingestion, "get_settings", lambda: _configs(tmp_path))
    monkeypatch.setattr(ingestion, "MiyousheClient", FakeMiyousheClient)
    db = _mock_db()

    result = ingestion.ingest_miyoushe(db)

    assert result["forums"] == 1
    assert result["inserted"] == 1
    assert result["duplicate"] == 0

    added = [call.args[0] for call in db.add.call_args_list]
    article = next(item for item in added if isinstance(item, Article))
    assert article.original_url == "https://www.miyoushe.com/ys/article/77385104"
    assert article.content_type == "community_post"
    assert "12-1 打不过" in article.content
    assert "回复 2" in article.content


def test_ingest_miyoushe_skips_duplicate_posts(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ingestion, "get_settings", lambda: _configs(tmp_path))
    monkeypatch.setattr(ingestion, "MiyousheClient", FakeMiyousheClient)
    db = _mock_db()
    # sync_topics, source lookup (feed_url then name) miss; the article URL
    # lookup hits an existing row so the post is counted as duplicate.
    db.scalar.side_effect = [None, None, None, 1]

    result = ingestion.ingest_miyoushe(db)

    assert result["inserted"] == 0
    assert result["duplicate"] == 1
