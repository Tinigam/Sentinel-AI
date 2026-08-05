from uuid import uuid4

from sqlalchemy.orm import Session

from app.services.retrieval import hybrid_search


def validate_citations(summary_points: list[dict], source_ids: set[str]) -> list[dict]:
    validated = []
    for point in summary_points:
        citations = [item for item in point["citation_ids"] if item in source_ids]
        if citations:
            validated.append({"claim": point["claim"], "citation_ids": citations})
    return validated


def answer_question(db: Session, question: str, topic: str | None, sentiment: str | None) -> dict:
    retrieval = hybrid_search(db, query=question, topic=topic, sentiment=sentiment, limit=5)
    sources = [
        {
            "id": f"article_{item['article_id']}",
            "title": item["title"],
            "source": item["source"],
            "published_at": item["published_at"],
            "url": item["url"],
            "snippet": item["snippet"],
        }
        for item in retrieval["results"]
    ]
    source_ids = {item["id"] for item in sources}
    if not sources:
        return {
            "answer": "当前数据库中没有足够的相关新闻证据来回答该问题。",
            "summary_points": [],
            "sources": [],
            "query": {"topic": topic, "sentiment": sentiment},
            "retrieval": {**retrieval, "source_count": 0},
            "insufficient_evidence": True,
            "request_id": str(uuid4()),
            "generator": "extractive.v1",
        }
    points = []
    answer_lines = ["以下内容仅基于当前数据库中的检索证据："]
    for source in sources[:3]:
        claim = f"《{source['title']}》的相关片段指出：{source['snippet']}"
        points.append({"claim": claim, "citation_ids": [source["id"]]})
        answer_lines.append(f"- {claim} [{source['id']}]")
    summary_points = validate_citations(points, source_ids)
    return {
        "answer": "\n".join(answer_lines),
        "summary_points": summary_points,
        "sources": sources,
        "query": {"topic": topic, "sentiment": sentiment},
        "retrieval": {
            "method": retrieval["method"],
            "candidate_count": retrieval["candidate_count"],
            "source_count": len(sources),
        },
        "insufficient_evidence": False,
        "request_id": str(uuid4()),
        "generator": "extractive.v1",
    }
