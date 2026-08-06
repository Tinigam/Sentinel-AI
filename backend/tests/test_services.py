import math

from app.services.indexing import EMBEDDING_DIMENSIONS, chunk_text, embed
from app.services.rag import validate_citations
from app.services.sentiment import classify_text


def test_chunk_text_normalizes_whitespace_and_preserves_content() -> None:
    assert chunk_text("  alpha\n\n beta  ", size=7) == ["alpha b", "eta"]


def test_embed_has_expected_dimension_and_unit_norm() -> None:
    vector = embed("Genshin update announcement")
    assert len(vector) == EMBEDDING_DIMENSIONS
    assert math.isclose(sum(value * value for value in vector), 1.0, rel_tol=1e-9)


def test_embed_empty_text_is_zero_vector() -> None:
    assert embed("") == [0.0] * EMBEDDING_DIMENSIONS


def test_sentiment_baseline_returns_explainable_negative_result() -> None:
    label, score, confidence, reason = classify_text("A launch bug caused a service issue.")
    assert label == "negative"
    assert score < 0
    assert confidence > 0.45
    assert "bug" in reason


def test_citation_validation_removes_unknown_sources() -> None:
    points = [
        {"claim": "Supported", "citation_ids": ["article_a", "unknown"]},
        {"claim": "Unsupported", "citation_ids": ["unknown"]},
    ]
    assert validate_citations(points, {"article_a"}) == [
        {"claim": "Supported", "citation_ids": ["article_a"]}
    ]