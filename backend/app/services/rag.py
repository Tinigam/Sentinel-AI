from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import ArticleSentiment
from app.services.llm import LLMError, chat_json
from app.services.retrieval import hybrid_search
from app.services.sentiment import sentiment_model_name

GENERATOR_FALLBACK = "extractive.v1"

SYSTEM_PROMPT = (
    "你是二次元游戏舆情分析助手。只能基于给定证据回答用户问题,禁止编造证据中没有的信息。"
    "只输出 JSON 对象,格式为 {\"answer\": \"中文回答正文\", \"summary_points\": [...]}。"
    "summary_points 中每个元素为 {\"claim\": \"一个论点\", \"citation_ids\": [\"article_xxx\"]},"
    "citation_ids 只能来自给定证据的 id 列表,每个论点至少引用一条证据。"
    "如果证据不足以回答问题,answer 中说明证据不足,summary_points 输出空数组。"
)


def validate_citations(summary_points: list[dict], source_ids: set[str]) -> list[dict]:
    validated = []
    for point in summary_points:
        citations = [item for item in point["citation_ids"] if item in source_ids]
        if citations:
            validated.append({"claim": point["claim"], "citation_ids": citations})
    return validated


def _evidence_block(sources: list[dict]) -> str:
    lines = []
    for source in sources:
        published = source["published_at"] or "未知时间"
        lines.append(
            f"[{source['id']}] 标题:{source['title']} | 来源:{source['source']} | "
            f"发布时间:{published}\n片段:{source['snippet']}"
        )
    return "\n\n".join(lines)


def _answer_with_llm(question: str, sources: list[dict]) -> dict:
    payload = chat_json(
        SYSTEM_PROMPT,
        f"问题:{question}\n\n证据列表:\n{_evidence_block(sources)}",
        max_tokens=1500,
    )
    answer = str(payload.get("answer", "")).strip()
    raw_points = payload.get("summary_points")
    if not answer or not isinstance(raw_points, list):
        raise LLMError("LLM response missing answer or summary_points")
    source_ids = {source["id"] for source in sources}
    summary_points = validate_citations(
        [
            {"claim": str(point.get("claim", "")), "citation_ids": list(point.get("citation_ids") or [])}
            for point in raw_points
            if isinstance(point, dict)
        ],
        source_ids,
    )
    return {"answer": answer, "summary_points": summary_points}


def _answer_extractive(sources: list[dict]) -> dict:
    points = []
    answer_lines = ["以下内容仅基于当前数据库中的检索证据："]
    for source in sources[:3]:
        claim = f"《{source['title']}》的相关片段指出:{source['snippet']}"
        points.append({"claim": claim, "citation_ids": [source["id"]]})
        answer_lines.append(f"- {claim} [{source['id']}]")
    return {
        "answer": "\n".join(answer_lines),
        "summary_points": validate_citations(points, {item["id"] for item in sources}),
    }


def answer_question(db: Session, question: str, topic: str | None, sentiment: str | None) -> dict:
    retrieval = hybrid_search(db, query=question, topic=topic, sentiment=sentiment, limit=5)
    sentiments: dict[str, dict] = {}
    article_ids = [item["article_id"] for item in retrieval["results"]]
    if article_ids:
        rows = db.execute(
            select(
                ArticleSentiment.article_id, ArticleSentiment.label, ArticleSentiment.score
            ).where(
                ArticleSentiment.article_id.in_(article_ids),
                ArticleSentiment.model_name == sentiment_model_name(),
            )
        ).all()
        for row in rows:
            sentiments.setdefault(
                str(row.article_id), {"label": row.label, "score": row.score}
            )
    sources = [
        {
            "id": f"article_{item['article_id']}",
            "title": item["title"],
            "source": item["source"],
            "published_at": item["published_at"],
            "url": item["url"],
            "snippet": item["snippet"],
            "sentiment": sentiments.get(item["article_id"]),
        }
        for item in retrieval["results"]
    ]
    retrieval_meta = {
        "method": retrieval["method"],
        "candidate_count": retrieval["candidate_count"],
        "source_count": len(sources),
    }
    base = {
        "sources": sources,
        "query": {"topic": topic, "sentiment": sentiment},
        "request_id": str(uuid4()),
    }
    if not sources:
        return {
            "answer": "当前数据库中没有足够的相关新闻证据来回答该问题。",
            "summary_points": [],
            **base,
            "retrieval": {**retrieval_meta, "source_count": 0},
            "insufficient_evidence": True,
            "generator": GENERATOR_FALLBACK,
        }
    generator = GENERATOR_FALLBACK
    if get_settings().openai_api_key:
        try:
            generated = _answer_with_llm(question, sources)
            generator = f"llm:{get_settings().llm_model}"
        except LLMError:
            generated = _answer_extractive(sources)
    else:
        generated = _answer_extractive(sources)
    return {
        **generated,
        **base,
        "retrieval": retrieval_meta,
        "insufficient_evidence": not generated["summary_points"],
        "generator": generator,
    }
