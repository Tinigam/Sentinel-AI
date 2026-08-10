from app.collectors import official_pages
from app.collectors.official_pages import _Links, extract_title, html_to_text


def test_link_parser_collects_anchor_text() -> None:
    parser = _Links()
    parser.feed('<a href="/news/42"> Version update <strong>notice</strong> </a>')
    assert parser.items == [("/news/42", "Version update notice")]


def test_html_to_text_skips_scripts_and_normalizes_whitespace() -> None:
    document = "<html><head><style>body{color:red}</style></head><body><h1>版本更新公告</h1><script>track()</script><p>  新角色   上线 </p></body></html>"
    assert html_to_text(document) == "版本更新公告 新角色 上线"


def test_discover_announcements_falls_back_to_embedded_urls(monkeypatch) -> None:
    document = (
        '<html><body><script>self.__next_f.push(["","{\\"url\\":\\"https://example.com/news/5104\\"}"])</script>'
        '<a href="/#index">HOME</a></body></html>'
    )
    monkeypatch.setattr(official_pages, "fetch_html", lambda url: document)

    assert official_pages.discover_announcements("https://example.com/news") == [
        {"title": "", "url": "https://example.com/news/5104"}
    ]


def test_extract_title_reads_title_tag() -> None:
    assert extract_title("<html><head><title> 版本更新 公告 - 明日方舟 </title></head></html>") == "版本更新 公告 - 明日方舟"