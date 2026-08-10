"""Community comment analysis: copypasta detection, per-user voting, concentration.

Detects opinion-volume distortion of the kind where a small group of highly
active users and duplicated template comments dominate the visible sentiment of
a video's comment section. Metrics are stored on Article.comment_metrics.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict

from app.services.sentiment import classify_text

TEMPLATE_MIN_CLUSTER = 3
NORMALIZED_PREFIX = 50
# CooRTweet-style coordination: same template from many distinct users inside
# a short time window is a strong brigading signal.
COORDINATION_WINDOW_SECONDS = 1800
COORDINATION_MIN_USERS = 5

# CJK chars, letters and digits are kept; emoji, punctuation and whitespace are dropped.
_STRIP_RE = re.compile(r"[^\w一-鿿]+")


def normalize_message(message: str) -> str:
    return _STRIP_RE.sub("", message).casefold()[:NORMALIZED_PREFIX]


def template_key(message: str) -> str:
    return hashlib.sha256(normalize_message(message).encode()).hexdigest()[:16]


def gini(counts: list[int]) -> float:
    """Gini coefficient of per-user comment counts; 0 = evenly spread, 1 = one user."""
    values = sorted(counts)
    total = sum(values)
    n = len(values)
    if total == 0 or n == 0:
        return 0.0
    weighted = sum((index + 1) * value for index, value in enumerate(values))
    return (2 * weighted) / (n * total) - (n + 1) / n


def top_share(counts: list[int], fraction: float) -> float:
    """Share of comments produced by the top `fraction` of users (e.g. 0.05 = top 5%)."""
    if not counts:
        return 0.0
    ordered = sorted(counts, reverse=True)
    head = max(1, int(len(ordered) * fraction))
    return sum(ordered[:head]) / sum(ordered)


def user_voted_sentiment(messages_by_user: dict[str, list[str]]) -> dict[str, int]:
    """One user, one vote: label each comment, then take each user's majority label."""
    votes = {"positive": 0, "neutral": 0, "negative": 0}
    for messages in messages_by_user.values():
        labels = Counter(classify_text(message)[0] for message in messages)
        votes[labels.most_common(1)[0][0]] += 1
    return votes


def raw_sentiment(messages: list[str]) -> dict[str, int]:
    counts = {"positive": 0, "neutral": 0, "negative": 0}
    for message in messages:
        counts[classify_text(message)[0]] += 1
    return counts


def like_weighted_sentiment(comments: list[dict]) -> dict[str, float]:
    """Each comment weighted by (1 + likes); contrast with raw_sentiment exposes brigading."""
    totals = {"positive": 0.0, "neutral": 0.0, "negative": 0.0}
    for comment in comments:
        totals[classify_text(comment["message"])[0]] += 1.0 + comment.get("like", 0)
    return totals


def coordinated_template_burst(comments: list[dict]) -> int:
    """Max distinct users posting the same template within one sliding window.
    Comments without a timestamp are ignored (metric stays 0 for legacy data)."""
    by_template: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for comment in comments:
        ctime = comment.get("ctime")
        if ctime:
            by_template[template_key(comment["message"])].append((int(ctime), comment["user_mid"]))
    max_users = 0
    for entries in by_template.values():
        if len({user for _, user in entries}) < TEMPLATE_MIN_CLUSTER:
            continue
        entries.sort()
        for index, (start, _) in enumerate(entries):
            window_users = {
                user
                for ts, user in entries[index:]
                if ts - start <= COORDINATION_WINDOW_SECONDS
            }
            max_users = max(max_users, len(window_users))
    return max_users


def compute_comment_metrics(comments: list[dict]) -> dict:
    """Full distortion report for one video's comment section."""
    if not comments:
        return {"total_comments": 0, "distortion_flags": ["no_comments"]}

    per_user: dict[str, list[str]] = defaultdict(list)
    for comment in comments:
        per_user[comment["user_mid"]].append(comment["message"])

    user_counts = [len(messages) for messages in per_user.values()]

    clusters = Counter(template_key(comment["message"]) for comment in comments)
    template_clusters = {key: size for key, size in clusters.items() if size >= TEMPLATE_MIN_CLUSTER}
    template_comments = sum(template_clusters.values())
    examples: dict[str, dict] = {}
    for comment in comments:
        key = template_key(comment["message"])
        if key in template_clusters and key not in examples:
            examples[key] = {"text": comment["message"][:80], "count": template_clusters[key]}
    top_templates = sorted(examples.values(), key=lambda item: item["count"], reverse=True)[:5]

    total = len(comments)
    template_share = template_comments / total
    top5 = top_share(user_counts, 0.05)
    flags = []
    if template_share > 0.3:
        flags.append("copypasta_brigade")
    if top5 > 0.7:
        flags.append("high_concentration")
    raw = raw_sentiment([comment["message"] for comment in comments])
    weighted = like_weighted_sentiment(comments)
    raw_negative = raw["negative"] / total
    weighted_total = sum(weighted.values())
    weighted_negative = weighted["negative"] / weighted_total if weighted_total else 0.0
    if raw_negative - weighted_negative > 0.15:
        flags.append("like_divergence")
    coordinated_users = coordinated_template_burst(comments)
    if coordinated_users >= COORDINATION_MIN_USERS:
        flags.append("coordinated_burst")

    return {
        "total_comments": total,
        "unique_users": len(per_user),
        "gini": round(gini(user_counts), 4),
        "top1_share": round(top_share(user_counts, 0.01), 4),
        "top5_share": round(top5, 4),
        "template_share": round(template_share, 4),
        "top_templates": top_templates,
        "coordinated_max_users": coordinated_users,
        "coordination_window_minutes": COORDINATION_WINDOW_SECONDS // 60,
        "sentiment_raw": raw,
        "sentiment_user_voted": user_voted_sentiment(per_user),
        "sentiment_like_weighted": {key: round(value, 1) for key, value in weighted.items()},
        "distortion_flags": flags,
    }
