from dataclasses import dataclass

GUIDE_TERMS = (
    "攻略",
    "配队",
    "培养",
    "怎么打",
    "任务",
    "配装",
    "材料",
    "武器推荐",
    "角色推荐",
    "攻略大全",
)
ESPORTS_TERMS = ("赛事", "联赛", "战队", "选手", "IVL", "KPL", "冠军", "直播赛")


@dataclass(frozen=True)
class ContentClassification:
    content_type: str
    is_intelligence: bool
    reason: str


def classify_content_type(title: str, summary: str, source_type: str) -> ContentClassification:
    if source_type == "official":
        return ContentClassification("official_announcement", True, "Configured official source.")
    text = f"{title}\n{summary}".casefold()
    matched_guide = next((term for term in GUIDE_TERMS if term.casefold() in text), None)
    if matched_guide:
        return ContentClassification("guide", False, f"Matched guide term: {matched_guide}.")
    matched_esports = next((term for term in ESPORTS_TERMS if term.casefold() in text), None)
    if matched_esports:
        return ContentClassification("esports", False, f"Matched esports term: {matched_esports}.")
    return ContentClassification("media_news", True, "Defaulted to news from a non-official source.")