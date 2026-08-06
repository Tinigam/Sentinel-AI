from app.services.content import classify_content_type


def test_official_source_is_an_official_announcement() -> None:
    result = classify_content_type("Any title", "", "official")
    assert result.content_type == "official_announcement"
    assert result.is_intelligence is True


def test_guide_content_is_excluded_from_intelligence() -> None:
    result = classify_content_type("角色培养攻略", "", "media")
    assert result.content_type == "guide"
    assert result.is_intelligence is False


def test_esports_content_is_excluded_from_intelligence() -> None:
    result = classify_content_type("联赛冠军诞生", "", "media")
    assert result.content_type == "esports"
    assert result.is_intelligence is False


def test_regular_media_content_remains_in_intelligence() -> None:
    result = classify_content_type("Version update announced", "Release date confirmed", "media")
    assert result.content_type == "media_news"
    assert result.is_intelligence is True