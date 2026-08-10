from app.services.community import (
    COORDINATION_MIN_USERS,
    COORDINATION_WINDOW_SECONDS,
    compute_comment_metrics,
    coordinated_template_burst,
    gini,
    normalize_message,
    template_key,
    top_share,
    user_voted_sentiment,
)


def test_normalize_strips_emoji_punctuation_and_truncates() -> None:
    assert normalize_message("重做异格银灰❗❗ 重做!!") == "重做异格银灰重做"
    assert normalize_message("a" * 100) == "a" * 50


def test_template_key_ignores_surface_variation() -> None:
    assert template_key("支持！") == template_key("支持❗❗❗")
    assert template_key("支持") != template_key("反对")


def test_gini_bounds() -> None:
    assert gini([1, 1, 1, 1]) == 0.0
    assert gini([100, 1, 1, 1]) > 0.7
    assert gini([]) == 0.0


def test_top_share() -> None:
    assert top_share([10, 1, 1, 1, 1, 1, 1, 1, 1, 1], 0.1) == 10 / 19
    assert top_share([], 0.05) == 0.0


def test_user_voted_sentiment_gives_each_user_one_vote() -> None:
    votes = user_voted_sentiment(
        {
            "spammer": ["差评 差评 差评", "差评", "投诉 差评"],
            "normal_a": ["好评"],
            "normal_b": ["更新 上线"],
        }
    )
    assert votes == {"positive": 2, "neutral": 0, "negative": 1}


def test_metrics_flags_copypasta_and_concentration() -> None:
    comments = []
    # 8 users x 5 identical copypasta comments (negative term 差评)
    for user in range(8):
        for _ in range(5):
            comments.append({"user_mid": f"u{user}", "message": "太差了差评 差评 差评", "like": 0})
    # 60 organic, lightly-liked comments from 60 distinct users
    for user in range(60):
        comments.append({"user_mid": f"org{user}", "message": f"期待新版本更新 {user}", "like": 20})

    metrics = compute_comment_metrics(comments)

    assert metrics["total_comments"] == 100
    assert metrics["unique_users"] == 68
    assert metrics["template_share"] > 0.3
    assert "copypasta_brigade" in metrics["distortion_flags"]
    assert metrics["top_templates"][0]["count"] == 40
    # like weighting shifts the balance positive: raw negative share is high
    assert metrics["sentiment_raw"]["negative"] == 40
    assert metrics["sentiment_like_weighted"]["positive"] > metrics["sentiment_like_weighted"]["negative"]


def test_metrics_empty() -> None:
    assert compute_comment_metrics([]) == {"total_comments": 0, "distortion_flags": ["no_comments"]}


def test_coordinated_burst_flags_same_template_in_short_window() -> None:
    base = 1_700_000_000
    comments = [
        {
            "user_mid": f"brigader{user}",
            "message": "策划道歉！重做异格！",
            "like": 0,
            "ctime": base + user * 120,  # 6 users within 12 minutes
        }
        for user in range(COORDINATION_MIN_USERS + 1)
    ]
    assert coordinated_template_burst(comments) == COORDINATION_MIN_USERS + 1
    metrics = compute_comment_metrics(comments)
    assert "coordinated_burst" in metrics["distortion_flags"]
    assert metrics["coordinated_max_users"] == COORDINATION_MIN_USERS + 1


def test_coordinated_burst_ignores_spread_out_and_missing_timestamps() -> None:
    base = 1_700_000_000
    spread = [
        {
            "user_mid": f"user{user}",
            "message": "策划道歉！重做异格！",
            "like": 0,
            "ctime": base + user * (COORDINATION_WINDOW_SECONDS + 60),
        }
        for user in range(6)
    ]
    assert coordinated_template_burst(spread) == 1
    legacy = [{"user_mid": f"user{user}", "message": "同上", "like": 0} for user in range(6)]
    assert coordinated_template_burst(legacy) == 0
