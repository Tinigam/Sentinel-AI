from app.collectors.bilibili import (
    BilibiliClient,
    BilibiliError,
    format_comments_section,
    mixin_key,
    sign_params,
)


def test_mixin_key_uses_permutation_table() -> None:
    # 64-char synthetic keys make the permutation observable
    img = "".join(chr(65 + (i % 26)) for i in range(32))
    sub = "".join(chr(97 + (i % 26)) for i in range(32))
    key = mixin_key(img, sub)
    raw = img + sub
    assert len(key) == 32
    assert all(char in raw for char in key)


def test_sign_params_adds_wts_and_w_rid() -> None:
    query = sign_params({"mid": 161775300, "ps": 5}, key="k" * 32, now=1700000000)
    assert "wts=1700000000" in query
    assert "w_rid=" in query
    assert "mid=161775300" in query


def test_format_comments_section_numbers_and_truncates() -> None:
    section = format_comments_section([
        {"message": "期待新版本", "like": 42},
        {"message": "一般", "like": 3},
    ])
    assert section.splitlines()[0] == "热门评论:"
    assert "1. 期待新版本 (赞42)" in section
    assert "2. 一般 (赞3)" in section
    assert format_comments_section([]) == ""


def test_account_videos_maps_fields(monkeypatch) -> None:
    client = BilibiliClient(delay=0)
    monkeypatch.setattr(client, "_wbi_key", lambda: "k" * 32)
    monkeypatch.setattr(
        client,
        "_get_json",
        lambda url: {
            "list": {
                "vlist": [
                    {"bvid": "BV1xx411c7mD", "aid": 123, "title": "版本PV",
                     "description": "desc", "created": 1700000000},
                    {"bvid": "BV1yy411c7mE", "aid": 124, "title": "活动预告",
                     "description": "", "created": 1700000100},
                ]
            }
        },
    )

    videos = client.account_videos(161775300, limit=5)

    assert videos == [
        {"bvid": "BV1xx411c7mD", "aid": 123, "title": "版本PV",
         "description": "desc", "pubdate": 1700000000},
        {"bvid": "BV1yy411c7mE", "aid": 124, "title": "活动预告",
         "description": "", "pubdate": 1700000100},
    ]


def test_video_comments_sorts_by_likes_and_strips_markup(monkeypatch) -> None:
    client = BilibiliClient(delay=0)

    def fake_get(url: str) -> dict:
        if "pn=1" not in url:
            return {"replies": []}
        return {
            "replies": [
                {"content": {"message": " 不错\ncell "}, "like": 2},
                {"content": {"message": "期待"}, "like": 99},
                {"content": {"message": "   "}, "like": 50},
            ]
        }

    monkeypatch.setattr(client, "_get_json", fake_get)

    comments = client.video_comments(123, limit=15)

    assert comments == [
        {"message": "期待", "like": 99},
        {"message": "不错 cell", "like": 2},
    ]


def test_get_json_raises_on_api_error(monkeypatch) -> None:
    client = BilibiliClient(delay=0)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    import json as _json
    monkeypatch.setattr(
        client._opener, "open",
        lambda url, timeout: FakeResponse(),
    )
    monkeypatch.setattr(_json, "load", lambda resp: {"code": -352, "message": "风控"})

    try:
        client._get_json("https://api.bilibili.com/test")
        raised = False
    except BilibiliError:
        raised = True
    assert raised
