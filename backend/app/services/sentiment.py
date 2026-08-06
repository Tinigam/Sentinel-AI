from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Article, ArticleSentiment, ArticleTopic, Source
from app.services.content import classify_content_type

POSITIVE_TERMS = ("获奖", "增长", "突破", "好评", "上线", "更新", "联动", "成功", "合作", "喜报")
NEGATIVE_TERMS = (
    "争议",
    "延期",
    "下架",
    "投诉",
    "差评",
    "负面",
    "裁员",
    "退款",
    "停服",
    "事故",
    "bug",
    "问题",
    "批评",
)
MODEL_NAME = "heuristic.v1"


def classify_text(text: str) -> tuple[str, float, float, str]:
    normalized = text.casefold()
    positive = [term for term in POSITIVE_TERMS if term.casefold() in normalized]
    negative = [term for term in NEGATIVE_TERMS if term.casefold() in normalized]
    score = min(1.0, 0.25 * len(positive)) - min(1.0, 0.25 * len(negative))
    if score > 0:
        label = "positive"
        evidence = positive
    elif score < 0:
        label = "negative"
        evidence = negative
    else:
        label = "neutral"
        evidence = []
    confidence = min(0.8, 0.45 + 0.12 * len(evidence)) if evidence else 0.35
    reason = (
        f"Matched impact terms: {', '.join(evidence)}."
        if evidence
        else "No baseline impact terms matched."
    )
    return label, score, confidence, reason


def classify_article(db: Session, article: Article) -> int:
    text = f"{article.title}\n{article.summary or ''}\n{article.content or ''}"
    created = 0
    for link in article.topic_links:
        exists = db.scalar(
            select(ArticleSentiment.id).where(
                ArticleSentiment.article_id == article.id,
                ArticleSentiment.topic_id == link.topic_id,
                ArticleSentiment.model_name == MODEL_NAME,
            )
        )
        if exists:
            continue
        label, score, confidence, reason = classify_text(text)
        db.add(
            ArticleSentiment(
                article_id=article.id,
                topic_id=link.topic_id,
                label=label,
                score=score,
                confidence=confidence,
                reason=reason,
                model_name=MODEL_NAME,
            )
        )
        created += 1
    return created


def classify_unprocessed(db: Session) -> dict[str, int]:
    content_types: dict[str, int] = {}
    rows = db.execute(select(Article, Source.source_type).join(Source)).all()
    for article, source_type in rows:
        result = classify_content_type(article.title, article.summary or "", source_type)
        article.content_type = result.content_type
        article.is_intelligence = result.is_intelligence
        content_types[result.content_type] = content_types.get(result.content_type, 0) + 1
    articles = db.scalars(
        select(Article).join(ArticleTopic).where(Article.is_intelligence.is_(True)).distinct()
    ).all()
    classified = sum(classify_article(db, article) for article in articles)
    db.commit()
    return {"classified": classified, **content_types}