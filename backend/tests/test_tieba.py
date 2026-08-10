import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.collectors.tieba import (
    SIGN_SUFFIX,
    TiebaClient,
    TiebaError,
    format_replies_section,
    sign_params,
)
from app.models.entities import Article, CommunityComment
from app.services import ingestion


def test_sign_params_sorts_keys_and_appends_suffix() -> None:
    signed = sign_params({"pn": 0, "kw": "原神"})
    payload = f"kw=原神pn=0{SIGN_SUFFIX}"
    assert signed["sign"] == hashlib.md5(payload.encode()).hexdigest()
    assert signed["pn"] == "0"


def test_sign_params_does_not_mutate_input() -> None:
    params = {"kz": "12345"}
    sign_params(params)
    assert params == {"kz": "12345"}


def test_format_replies_section_numbers_and_handles_empty() -> None:
    section = format_replies_section([
        {"message": "说得对", "like": 12},
        {"message": "路过", "like": 0},
    ])
    assert section.splitlines()[0] == "热门回复:"
    assert "1. 说得对 (赞12)" in section
    assert "2. 路过 (赞0)" in section
    assert format_replies_section([]) == ""


def test_forum_threads_maps_fields(monkeypatch) -> None:
    client = TiebaClient(delay=0)
    captured: dict = {}
    payload = {
        "thread_list": [
            {"id": 123456, "title": "新版本吐槽", "reply_num": "42",
             "author_id": 555, "create_time": "1700000000"},
            {"id": 123457, "title": "晒卡", "reply_num": 3,
             "author_id": 556, "create_time": 1700000100},
        ],
        "user_list": [
            {"id": 555, "name": "楼主甲本体", "name_show": "楼主甲"},
            {"id": 556, "name": "楼主乙"},
        ],
    }

    def fake_post(path: str, params: dict) -> dict:
        captured["path"] = path
        captured["params"] = params
        return payload

    monkeypatch.setattr(client, "_post_json", fake_post)

    threads = client.forum_threads("原神", limit=20)

    assert captured["path"] == "/c/f/frs/page"
    assert captured["params"]["kw"] == "原神"
    assert threads == [
        {"tid": "123456", "title": "新版本吐槽", "reply_num": 42,
         "author": "楼主甲", "create_time": 1700000000},
        {"tid": "123457", "title": "晒卡", "reply_num": 3,
         "author": "楼主乙", "create_time": 1700000100},
    ]


def test_thread_posts_uses_kz_and_parses_floors(monkeypatch) -> None:
    client = TiebaClient(delay=0)
    captured: dict = {}
    payload = {
        "post_list": [
            {"id": 11, "floor": 1, "time": "1700000000", "author_id": 777,
             "content": [{"type": 0, "text": "一楼  正文"}, {"type": 3}],
             "agree": {"agree_num": 7}},
            {"id": 12, "floor": 2, "time": 1700000100, "author_id": 778,
             "content": [{"type": 0, "text": "顶"}],
             "agree": {"agree_num": 3}},
            {"id": 13, "floor": 3, "time": 1700000200, "author_id": 779,
             "content": [{"type": 1}]},
        ],
        "user_list": [
            {"id": 777, "name": "楼主", "name_show": "楼主", "portrait": "tb.1.abc"},
            {"id": 778, "name": "甲"},
            {"id": 779, "name": "乙"},
        ],
    }

    def fake_post(path: str, params: dict) -> dict:
        captured["path"] = path
        captured["params"] = params
        if params.get("pn", 1) != 1:
            return {"post_list": []}
        return payload

    monkeypatch.setattr(client, "_post_json", fake_post)

    posts = client.thread_posts("123456", limit=30)

    assert captured["path"] == "/c/f/pb/page"
    assert captured["params"]["kz"] == "123456"
    assert "tid" not in captured["params"]
    assert posts == [
        {"post_id": "11", "floor": 1, "author": "楼主", "user_key": "tb.1.abc",
         "text": "一楼 正文", "like": 7, "time": 1700000000},
        {"post_id": "12", "floor": 2, "author": "甲", "user_key": "甲",
         "text": "顶", "like": 3, "time": 1700000100},
    ]


def test_thread_posts_paginates_when_reply_page_is_full(monkeypatch) -> None:
    client = TiebaClient(delay=0)
    calls: list[int] = []

    def make_post(post_id: int, floor: int) -> dict:
        return {"id": post_id, "floor": floor, "time": 1700000000 + floor,
                "author_id": 777, "content": [{"type": 0, "text": f"楼层{floor}"}],
                "agree": {"agree_num": floor}}

    def fake_post(path: str, params: dict) -> dict:
        calls.append(params["pn"])
        if params["pn"] == 1:
            # server page cap: 30 posts including floor 1 -> only 29 replies
            return {"post_list": [make_post(1000 + floor, floor) for floor in range(1, 31)],
                    "user_list": [{"id": 777, "name": "用户"}]}
        return {"post_list": [make_post(1031, 31)],
                "user_list": [{"id": 777, "name": "用户"}]}

    monkeypatch.setattr(client, "_post_json", fake_post)

    posts = client.thread_posts("123456", limit=30)

    assert calls == [1, 2]
    assert len([post for post in posts if post["floor"] > 1]) == 30


def test_post_json_raises_on_api_error(monkeypatch) -> None:
    client = TiebaClient(delay=0)
    monkeypatch.setattr("app.collectors.tieba.RETRY_DELAY_SECONDS", 0)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"error_code": "350004", "error_msg": "param error"}'

    monkeypatch.setattr(client._opener, "open", lambda request, timeout: FakeResponse())

    try:
        client._post_json("/c/f/pb/page", {"kz": "1"})
        raised = False
    except TiebaError as error:
        raised = True
        assert "350004" in str(error)
    assert raised


def test_load_tieba_forums_reads_enabled_entries(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / "sources.yaml"
    config.write_text(
        "tieba_forums:\n"
        "  - name: 明日方舟吧\n"
        "    kw: 明日方舟\n"
        "    topic: arknights\n"
        "  - name: 已禁用吧\n"
        "    enabled: false\n"
        "    kw: 禁用\n"
        "    topic: arknights\n",
        encoding="utf-8",
    )
    settings = SimpleNamespace(sources_config_path=config)
    monkeypatch.setattr(ingestion, "get_settings", lambda: settings)

    assert ingestion.load_tieba_forums() == [
        {
            "name": "明日方舟吧",
            "kw": "明日方舟",
            "topic": "arknights",
            "source_type": "community",
            "trust_tier": "unverified",
        }
    ]


class FakeTiebaClient:
    def forum_threads(self, kw: str, limit: int = 20) -> list[dict]:
        return [
            {"tid": "100", "title": "版本讨论", "reply_num": 42,
             "author": "楼主", "create_time": 1700000000},
            {"tid": "101", "title": "水帖", "reply_num": 2,
             "author": "路人", "create_time": 1700000000},
        ]

    def thread_posts(self, tid: str, limit: int = 30) -> list[dict]:
        assert tid == "100"
        posts = [
            {"post_id": "900", "floor": 1, "author": "楼主", "user_key": "portrait-lz",
             "text": "楼主正文", "like": 5, "time": 1700000000}
        ]
        for index in range(30):
            posts.append(
                {"post_id": f"9{index + 1:02d}", "floor": index + 2,
                 "author": f"用户{index}", "user_key": f"portrait-{index}",
                 "text": f"回复{index}", "like": index, "time": 1700000000 + index}
            )
        return posts


def _mock_db(comment_rows: list) -> MagicMock:
    db = MagicMock()
    db.scalar.return_value = None
    db.scalars.return_value.all.return_value = comment_rows
    return db


def test_ingest_tieba_inserts_articles_comments_and_metrics(monkeypatch, tmp_path: Path) -> None:
    topics_config = tmp_path / "topics.yaml"
    topics_config.write_text(
        "topics:\n  - {slug: arknights, display_name: 明日方舟, aliases: [], keywords: []}\n",
        encoding="utf-8",
    )
    sources_config = tmp_path / "sources.yaml"
    sources_config.write_text(
        "tieba_forums:\n  - {name: 明日方舟吧, kw: 明日方舟, topic: arknights}\n",
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        topics_config_path=topics_config,
        sources_config_path=sources_config,
        tieba_threads_per_forum=20,
        tieba_min_replies=5,
    )
    monkeypatch.setattr(ingestion, "get_settings", lambda: settings)
    monkeypatch.setattr(ingestion, "TiebaClient", FakeTiebaClient)
    comment_rows = [
        SimpleNamespace(user_mid=f"portrait-{index}", message=f"回复{index}",
                        like_count=index, published_at=None)
        for index in range(30)
    ]
    db = _mock_db(comment_rows)

    result = ingestion.ingest_tieba(db)

    assert result["forums"] == 1
    assert result["skipped_threads"] == 1  # reply_num 2 < tieba_min_replies 5
    assert result["inserted"] == 1
    assert result["duplicate"] == 0
    assert result["comments_stored"] == 30
    assert result["metrics_computed"] == 1

    added = [call.args[0] for call in db.add.call_args_list]
    article = next(item for item in added if isinstance(item, Article))
    assert article.original_url == "https://tieba.baidu.com/p/100"
    assert article.content_type == "community_post"
    assert "楼主正文" in article.content
    assert "热门回复:" in article.content
    assert "回复29 (赞29)" in article.content  # hot replies sorted by likes
    assert article.comment_metrics["total_comments"] == 30
    comments = [item for item in added if isinstance(item, CommunityComment)]
    assert len(comments) == 30
    assert all(item.platform == "tieba" for item in comments)
    assert {item.comment_id for item in comments} == {f"9{index + 1:02d}" for index in range(30)}


def test_ingest_tieba_skips_duplicate_threads(monkeypatch, tmp_path: Path) -> None:
    topics_config = tmp_path / "topics.yaml"
    topics_config.write_text(
        "topics:\n  - {slug: arknights, display_name: 明日方舟, aliases: [], keywords: []}\n",
        encoding="utf-8",
    )
    sources_config = tmp_path / "sources.yaml"
    sources_config.write_text(
        "tieba_forums:\n  - {name: 明日方舟吧, kw: 明日方舟, topic: arknights}\n",
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        topics_config_path=topics_config,
        sources_config_path=sources_config,
        tieba_threads_per_forum=20,
        tieba_min_replies=5,
    )
    monkeypatch.setattr(ingestion, "get_settings", lambda: settings)
    monkeypatch.setattr(ingestion, "TiebaClient", FakeTiebaClient)
    db = _mock_db([])
    # sync_topics, source lookup (feed_url then name) miss; the article URL
    # lookup hits an existing row so the thread is counted as duplicate.
    db.scalar.side_effect = [None, None, None, 1]

    result = ingestion.ingest_tieba(db)

    assert result["inserted"] == 0
    assert result["duplicate"] == 1
    assert result["comments_stored"] == 0
