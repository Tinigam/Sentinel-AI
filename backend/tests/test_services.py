import io
import json
import math
import uuid
from types import SimpleNamespace

import pytest

from app.services import indexing, rag, retrieval, sentiment
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


def test_rerank_maps_relevance_scores_by_document_index(monkeypatch) -> None:
    monkeypatch.setattr(
        retrieval,
        "get_settings",
        lambda: Settings(
            openai_api_key="test-key",
            rerank_model="test-rerank",
            rerank_base_url="https://example.com/rerank",
        ),
    )
    captured = {}

    def fake_urlopen(request, timeout=0):
        captured["body"] = json.loads(request.data.decode())
        payload = {
            "output": {
                "results": [
                    {"index": 2, "relevance_score": 0.91},
                    {"index": 0, "relevance_score": 0.42},
                    {"index": 1, "relevance_score": 0.07},
                ]
            }
        }
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(retrieval.urllib.request, "urlopen", fake_urlopen)
    assert retrieval._rerank("终末地二测", ["a", "b", "c"]) == [0.42, 0.07, 0.91]
    assert captured["body"]["model"] == "test-rerank"
    assert captured["body"]["input"]["query"] == "终末地二测"


def test_rerank_raises_immediately_on_client_error(monkeypatch) -> None:
    monkeypatch.setattr(
        retrieval,
        "get_settings",
        lambda: Settings(
            openai_api_key="test-key",
            rerank_model="test-rerank",
            rerank_base_url="https://example.com/rerank",
        ),
    )

    def failing_urlopen(request, timeout=0):
        raise retrieval.urllib.error.HTTPError(request.full_url, 400, "bad request", {}, None)

    monkeypatch.setattr(retrieval.urllib.request, "urlopen", failing_urlopen)
    with pytest.raises(retrieval.RerankError):
        retrieval._rerank("q", ["a"])


class _FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _FakeRecallSession:
    """Dispatches recall-lane statements by their SQL; no real Postgres needed."""

    def __init__(self, fts_ids: list, trigram_ids: list, vector_rows: list, articles: list) -> None:
        self.fts_ids = fts_ids
        self.trigram_ids = trigram_ids
        self.vector_rows = vector_rows
        self.articles = articles
        self.statements: list[str] = []

    def execute(self, statement: object) -> _FakeResult:
        sql = str(statement)
        self.statements.append(sql)
        if "websearch_to_tsquery" in sql:
            return _FakeResult([(article_id,) for article_id in self.fts_ids])
        if "similarity" in sql:
            return _FakeResult([(article_id,) for article_id in self.trigram_ids])
        return _FakeResult(self.vector_rows)

    def scalars(self, _statement: object) -> _FakeResult:
        return _FakeResult(self.articles)


def _article(article_id: uuid.UUID, title: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=article_id,
        title=title,
        content="原神新版本活动引发玩家讨论",
        summary=None,
        content_type="news",
        source_name="bilibili",
        published_at=None,
        original_url="https://example.com/a",
    )


def test_hybrid_search_trigram_lane_recalls_chinese_query_misses(monkeypatch) -> None:
    """FTS ('simple' tokenizer) misses a Chinese query; the trigram lane must
    still bring the relevant article into the RRF candidates."""
    _force_local_embedding(monkeypatch)
    monkeypatch.setattr(retrieval, "get_settings", lambda: Settings(openai_api_key=""))
    article_id = uuid.uuid4()
    session = _FakeRecallSession(
        fts_ids=[],  # websearch_to_tsquery('simple', ...) treats 中文整串为一个 lexeme
        trigram_ids=[article_id],
        vector_rows=[],
        articles=[_article(article_id, "原神新版本节奏汇总")],
    )

    result = retrieval.hybrid_search(session, "最近原神有什么节奏")

    assert result["method"] == "hybrid_rrf"
    assert [item["article_id"] for item in result["results"]] == [str(article_id)]
    assert result["candidate_count"] == 1
    assert any("similarity" in sql for sql in session.statements)


def test_recall_applies_topic_and_sentiment_filters_to_trigram_lane(monkeypatch) -> None:
    _force_local_embedding(monkeypatch)
    session = _FakeRecallSession(fts_ids=[], trigram_ids=[], vector_rows=[], articles=[])

    retrieval._recall(session, "原神 节奏", topic="genshin", sentiment="negative", model_name="m")

    trigram_sql = next(sql for sql in session.statements if "similarity" in sql)
    assert "article_topics" in trigram_sql
    assert "article_sentiments" in trigram_sql


class _FakeSentimentSession:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    def execute(self, _statement: object) -> _FakeResult:
        return _FakeResult(self.rows)


def _retrieval_payload(article_ids: list[uuid.UUID]) -> dict:
    return {
        "method": "hybrid_rrf",
        "candidate_count": len(article_ids),
        "results": [
            {
                "article_id": str(article_id),
                "title": f"标题 {index}",
                "source": "bilibili",
                "published_at": None,
                "url": "https://example.com/a",
                "snippet": "片段",
            }
            for index, article_id in enumerate(article_ids)
        ],
    }


def test_answer_question_attaches_sentiment_to_sources(monkeypatch) -> None:
    article_a, article_b = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(rag, "hybrid_search", lambda *args, **kwargs: _retrieval_payload([article_a, article_b]))
    monkeypatch.setattr(rag, "sentiment_model_name", lambda: "test-model")
    monkeypatch.setattr(rag, "get_settings", lambda: Settings(openai_api_key=""))
    rows = [SimpleNamespace(article_id=article_a, label="negative", score=-0.7)]

    result = rag.answer_question(_FakeSentimentSession(rows), "原神最近的节奏?", None, None)

    assert result["sources"][0]["sentiment"] == {"label": "negative", "score": -0.7}
    assert result["sources"][1]["sentiment"] is None


def test_answer_question_sources_sentiment_none_without_rows(monkeypatch) -> None:
    article_a = uuid.uuid4()
    monkeypatch.setattr(rag, "hybrid_search", lambda *args, **kwargs: _retrieval_payload([article_a]))
    monkeypatch.setattr(rag, "sentiment_model_name", lambda: "test-model")
    monkeypatch.setattr(rag, "get_settings", lambda: Settings(openai_api_key=""))

    result = rag.answer_question(_FakeSentimentSession([]), "原神最近的节奏?", None, None)

    assert result["sources"][0]["sentiment"] is None
