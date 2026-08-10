from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Article, ArticleSentiment, ArticleTopic, Source
from app.services.content import classify_content_type
from app.services.llm import LLMError, chat_json

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
HEURISTIC_MODEL_NAME = "heuristic.v1"
# Texts per chat call; each text is truncated to keep the prompt bounded.
LLM_BATCH_SIZE = 6
LLM_TEXT_LIMIT = 1200

SYSTEM_PROMPT = (
    "你是二次元游戏舆情分析助手。给定若干条关于游戏的新闻或社区文本,"
    "逐条判断文本对对应游戏厂商/运营方的舆论情感。"
    "只输出 JSON 对象,格式为 {\"results\": [...]},数组长度与输入条数一致,顺序对应。"
    "每个元素为 {\"label\": \"positive\"|\"neutral\"|\"negative\", "
    "\"score\": -1.0 到 1.0 的浮点数, \"confidence\": 0.0 到 1.0 的浮点数, "
    "\"reason\": \"不超过 50 字的中文理由\"}。"
)


def sentiment_model_name() -> str:
    settings = get_settings()
    return settings.llm_model if settings.openai_api_key else HEURISTIC_MODEL_NAME


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


def _clamp(value: object, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _classify_texts_llm(texts: list[str]) -> list[tuple[str, float, float, str]]:
    numbered = "\n\n".join(
        f"[{index + 1}] {text[:LLM_TEXT_LIMIT]}" for index, text in enumerate(texts)
    )
    payload = chat_json(SYSTEM_PROMPT, f"请对以下 {len(texts)} 条文本逐条判断情感:\n\n{numbered}")
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != len(texts):
        raise LLMError(f"Expected {len(texts)} results, got {type(results).__name__}")
    classified = []
    for item in results:
        label = str(item.get("label", "")).strip().lower()
        if label not in ("positive", "neutral", "negative"):
            raise LLMError(f"Invalid label in LLM response: {label!r}")
        classified.append(
            (
                label,
                _clamp(item.get("score"), -1.0, 1.0, 0.0),
                _clamp(item.get("confidence"), 0.0, 1.0, 0.5),
                str(item.get("reason", ""))[:500],
            )
        )
    return classified


def classify_texts(texts: list[str]) -> tuple[list[tuple[str, float, float, str]], str]:
    """Classify texts with the configured provider, returning (results, model_name).
    Falls back to the heuristic baseline when no API key is configured or the
    LLM fails, so ingestion stays alive; model_name reflects what ran."""
    if get_settings().openai_api_key:
        try:
            results: list[tuple[str, float, float, str]] = []
            for start in range(0, len(texts), LLM_BATCH_SIZE):
                results.extend(_classify_texts_llm(texts[start : start + LLM_BATCH_SIZE]))
            return results, get_settings().llm_model
        except LLMError:
            pass  # fall through to heuristic baseline
    return [classify_text(text) for text in texts], HEURISTIC_MODEL_NAME


def classify_articles(db: Session, articles: list[Article]) -> dict[str, int | str]:
    pending: dict[object, list[ArticleTopic]] = {}
    model_name = sentiment_model_name()
    for article in articles:
        links = [
            link
            for link in article.topic_links
            if not db.scalar(
                select(ArticleSentiment.id).where(
                    ArticleSentiment.article_id == article.id,
                    ArticleSentiment.topic_id == link.topic_id,
                    ArticleSentiment.model_name == model_name,
                )
            )
        ]
        if links:
            pending[article.id] = links
    if not pending:
        return {"classified": 0, "sentiment_model": model_name}
    to_classify = [article for article in articles if article.id in pending]
    texts = [
        f"{article.title}\n{article.summary or ''}\n{article.content or ''}"
        for article in to_classify
    ]
    results, used_model = classify_texts(texts)
    created = 0
    for article, (label, score, confidence, reason) in zip(to_classify, results, strict=True):
        for link in pending[article.id]:
            db.add(
                ArticleSentiment(
                    article_id=article.id,
                    topic_id=link.topic_id,
                    label=label,
                    score=score,
                    confidence=confidence,
                    reason=reason,
                    model_name=used_model,
                )
            )
            created += 1
    return {"classified": created, "sentiment_model": used_model}


def classify_article(db: Session, article: Article) -> int:
    return int(classify_articles(db, [article])["classified"])


def classify_unprocessed(db: Session) -> dict[str, int | str]:
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
    outcome = classify_articles(db, articles)
    db.commit()
    return {**outcome, **content_types}
