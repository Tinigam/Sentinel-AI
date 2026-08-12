from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.collectors.weibo import WeiboClient, parse_search_html, parse_weibo_time
from app.models.entities import Article
from app.services import ingestion

CARD_HTML = """
<div class="card-wrap" action-type="feed_list_item" mid="5230000000000001">
  <div class="card">
    <div class="card-feed">
      <div class="info">
        <div><a class="name" href="//weibo.com/u/1001">旅行者一号</a></div>
        <div class="from"><a href="//weibo.com/1001/AbCdEf?refer_flag=1001030103_" target="_blank">今天 08:15</a></div>
      </div>
      <div class="content" node-type="like">
        <p class="txt" node-type="feed_list_content" nick-name="旅行者一号">原神新地图太好看了</p>
      </div>
    </div>
    <div class="card-act">
      <ul>
        <li><a action-type="fl_forward">转发 12</a></li>
        <li><a action-type="fl_comment">评论 34</a></li>
        <li><a action-type="fl_like">56</a></li>
      </ul>
    </div>
  </div>
</div>
"""


def test_parse_weibo_time_relative_and_absolute() -> None:
    now = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    assert parse_weibo_time("3分钟前", now) == datetime(2026, 8, 12, 9, 57, tzinfo=UTC)
    assert parse_weibo_time("今天 08:15", now) == datetime(2026, 8, 12, 8, 15, tzinfo=UTC)
    assert parse_weibo_time("08月11日 22:30", now) == datetime(2026, 8, 11, 22, 30, tzinfo=UTC)
    assert parse_weibo_time("2025-08-11 22:30", now) == datetime(2025, 8, 11, 22, 30, tzinfo=UTC)
    assert parse_weibo_time("无法解析", now) is None


def test_parse_search_html_extracts_cards() -> None:
    posts = parse_search_html(CARD_HTML, limit=20)

    assert len(posts) == 1
    post = posts[0]
    assert post["mid"] == "5230000000000001"
    assert post["url"] == "https://weibo.com/1001/AbCdEf"
    assert post["author"] == "旅行者一号"
    assert post["text"] == "原神新地图太好看了"
    assert (post["repost"], post["comment"], post["like"]) == (12, 34, 56)
    assert post["published_at"] is not None


def test_parse_search_html_ignores_incomplete_cards() -> None:
    html = '<div action-type="feed_list_item" mid="1"><p class="txt">无链接</p></div>'
    assert parse_search_html(html) == []


def test_search_posts_raises_on_redirect(monkeypatch) -> None:
    from app.collectors.weibo import WeiboError

    client = WeiboClient(cookie="fake", delay=0)

    class FakeResponse:
        status_code = 302

    monkeypatch.setattr(client._client, "get", lambda *a, **k: FakeResponse())

    try:
        client.search_posts("原神")
        raised = False
    except WeiboError as error:
        raised = True
        assert "302" in str(error)
    assert raised


class FakeWeiboClient:
    def __init__(self, cookie: str):
        self.cookie = cookie

    def search_posts(self, query: str, limit: int = 20) -> list[dict]:
        assert query == "明日方舟"
        return [
            {
                "mid": "5230000000000001",
                "url": "https://weibo.com/1001/AbCdEf",
                "author": "刀客塔",
                "text": "明日方舟新活动开了",
                "repost": 12,
                "comment": 34,
                "like": 56,
                "published_at": datetime(2026, 8, 12, 8, 15, tzinfo=UTC),
            },
        ]


def _configs(tmp_path: Path) -> SimpleNamespace:
    topics_config = tmp_path / "topics.yaml"
    topics_config.write_text(
        "topics:\n  - {slug: arknights, display_name: 明日方舟, aliases: [], keywords: []}\n",
        encoding="utf-8",
    )
    sources_config = tmp_path / "sources.yaml"
    sources_config.write_text(
        "weibo_queries:\n  - {name: 微博搜索 明日方舟, query: 明日方舟, topic: arknights}\n",
        encoding="utf-8",
    )
    return SimpleNamespace(topics_config_path=topics_config, sources_config_path=sources_config)


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.scalar.return_value = None
    return db


def test_ingest_weibo_inserts_articles(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ingestion, "get_settings", lambda: _configs(tmp_path))
    monkeypatch.setattr(ingestion, "load_cookie_header", lambda site: "fake-cookie")
    monkeypatch.setattr(ingestion, "WeiboClient", FakeWeiboClient)
    db = _mock_db()

    result = ingestion.ingest_weibo(db)

    assert result["queries"] == 1
    assert result["inserted"] == 1
    assert result["duplicate"] == 0

    added = [call.args[0] for call in db.add.call_args_list]
    article = next(item for item in added if isinstance(item, Article))
    assert article.original_url == "https://weibo.com/1001/AbCdEf"
    assert article.content_type == "community_post"
    assert "明日方舟新活动开了" in article.content
    assert "转发 12" in article.content
    assert article.published_at == datetime(2026, 8, 12, 8, 15, tzinfo=UTC)


def test_ingest_weibo_skips_duplicate_posts(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ingestion, "get_settings", lambda: _configs(tmp_path))
    monkeypatch.setattr(ingestion, "load_cookie_header", lambda site: "fake-cookie")
    monkeypatch.setattr(ingestion, "WeiboClient", FakeWeiboClient)
    db = _mock_db()
    # sync_topics, source lookup (feed_url then name) miss; the article URL
    # lookup hits an existing row so the post is counted as duplicate.
    db.scalar.side_effect = [None, None, None, 1]

    result = ingestion.ingest_weibo(db)

    assert result["inserted"] == 0
    assert result["duplicate"] == 1


def test_ingest_weibo_skips_lane_without_session(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ingestion, "get_settings", lambda: _configs(tmp_path))
    monkeypatch.setattr(ingestion, "load_cookie_header", lambda site: None)
    db = _mock_db()

    result = ingestion.ingest_weibo(db)

    assert result["queries"] == 1
    assert result["inserted"] == 0
    added = [call.args[0] for call in db.add.call_args_list]
    assert not any(isinstance(item, Article) for item in added)
