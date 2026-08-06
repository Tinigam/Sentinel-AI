from app.collectors.official_pages import _Links


def test_link_parser_collects_anchor_text() -> None:
    parser = _Links()
    parser.feed('<a href="/news/42"> Version update <strong>notice</strong> </a>')
    assert parser.items == [("/news/42", "Version update notice")]