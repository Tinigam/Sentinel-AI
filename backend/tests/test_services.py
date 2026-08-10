import io
import json
import math

import pytest

from app.services import indexing, rag, sentiment
from app.core.config import Settings
from app.services.indexing import EMBEDDING_DIMENSIONS, chunk_text, embed
from app.services.llm import LLMError
from app.services.rag import validate_citations
from app.services.sentiment import classify_text


def _force_local_embedding(monkeypatch) -> None:
    monkeypatch.setattr(indexing, "get_settings", lambda: Settings(openai_api_key=""))


def test_chunk_text_normalizes_whitespace_and_preserves_content() -> None:
    assert chunk_text("  alpha\n\n beta  ", size=7) == ["alpha b", "eta"]


def test_embed_has_expected_dimension_and_unit_norm(monkeypatch) -> None:
    _force_local_embedding(monkeypatch)
    vector = embed("Genshin update announcement")
    assert len(vector) == EMBEDDING_DIMENSIONS
    assert math.isclose(sum(value * value for value in vector), 1.0, rel_tol=1e-9)


def test_embed_empty_text_is_zero_vector(monkeypatch) -> None:
    _force_local_embedding(monkeypatch)
    assert embed("") == [0.0] * EMBEDDING_DIMENSIONS


def test_embed_uses_remote_api_when_key_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        indexing,
        "get_settings",
        lambda: Settings(
            openai_api_key="test-key",
            llm_base_url="https://example.com/v1",
            embedding_model="test-embedding",
        ),
    )
    captured = {}

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode())
        payload = {"data": [{"embedding": [0.1] * EMBEDDING_DIMENSIONS}]}
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(indexing.urllib.request, "urlopen", fake_urlopen)
    assert embed("终末地 二测") == [0.1] * EMBEDDING_DIMENSIONS
    assert captured["url"] == "https://example.com/v1/embeddings"
    assert captured["body"]["model"] == "test-embedding"
    assert captured["body"]["input"] == ["终末地 二测"]
    assert captured["body"]["dimension"] == EMBEDDING_DIMENSIONS
    assert indexing.embedding_model_name() == "test-embedding"


def test_sentiment_baseline_returns_explainable_negative_result() -> None:
    label, score, confidence, reason = classify_text("A launch bug caused a service issue.")
    assert label == "negative"
    assert score < 0
    assert confidence > 0.45
    assert "bug" in reason


def test_classify_texts_uses_llm_when_key_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        sentiment,
        "get_settings",
        lambda: Settings(openai_api_key="test-key", llm_model="test-chat"),
    )
    captured = {}

    def fake_chat_json(system_prompt, user_prompt, max_tokens=2000):
        captured["user_prompt"] = user_prompt
        return {
            "results": [
                {"label": "negative", "score": -0.8, "confidence": 0.9, "reason": "玩家批评运营"},
                {"label": "positive", "score": 0.6, "confidence": 0.8, "reason": "版本好评"},
            ]
        }

    monkeypatch.setattr(sentiment, "chat_json", fake_chat_json)
    results, model_name = sentiment.classify_texts(["玩家怒喷新版本逼氪", "新角色广受好评"])
    assert model_name == "test-chat"
    assert [label for label, *_ in results] == ["negative", "positive"]
    assert "[1]" in captured["user_prompt"] and "[2]" in captured["user_prompt"]


def test_classify_texts_falls_back_to_heuristic_on_llm_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        sentiment,
        "get_settings",
        lambda: Settings(openai_api_key="test-key", llm_model="test-chat"),
    )

    def failing_chat_json(system_prompt, user_prompt, max_tokens=2000):
        raise LLMError("boom")

    monkeypatch.setattr(sentiment, "chat_json", failing_chat_json)
    results, model_name = sentiment.classify_texts(["A launch bug caused a service issue."])
    assert model_name == sentiment.HEURISTIC_MODEL_NAME
    assert results[0][0] == "negative"


def test_citation_validation_removes_unknown_sources() -> None:
    points = [
        {"claim": "Supported", "citation_ids": ["article_a", "unknown"]},
        {"claim": "Unsupported", "citation_ids": ["unknown"]},
    ]
    assert validate_citations(points, {"article_a"}) == [
        {"claim": "Supported", "citation_ids": ["article_a"]}
    ]


def test_answer_with_llm_keeps_only_known_citations(monkeypatch) -> None:
    def fake_chat_json(system_prompt, user_prompt, max_tokens=2000):
        return {
            "answer": "终末地二测舆论以正面为主 [article_a]",
            "summary_points": [
                {"claim": "二测评价正面", "citation_ids": ["article_a", "article_bogus"]},
                {"claim": "无法验证的论点", "citation_ids": ["article_bogus"]},
            ],
        }

    monkeypatch.setattr(rag, "chat_json", fake_chat_json)
    sources = [
        {
            "id": "article_a",
            "title": "终末地二测前瞻",
            "source": "bilibili",
            "published_at": None,
            "url": "https://example.com",
            "snippet": "玩家普遍期待",
        }
    ]
    result = rag._answer_with_llm("终末地二测舆论如何?", sources)
    assert result["summary_points"] == [
        {"claim": "二测评价正面", "citation_ids": ["article_a"]}
    ]
    assert "终末地" in result["answer"]


def test_answer_with_llm_rejects_malformed_response(monkeypatch) -> None:
    monkeypatch.setattr(rag, "chat_json", lambda *args, **kwargs: {"summary_points": []})
    with pytest.raises(LLMError):
        rag._answer_with_llm("问题", [])
