"""Miyoushe (米游社) collector: forum post lists via the anonymous bbs-api.

No login is required. ``getForumPostList`` returns posts whose ``content`` is a
JSON string (``{"describe": ..., "imgs": [...]}``); per-post comments are not
fetched, so only articles are stored. Style mirrors collectors/tieba.py
(throttled requests, one delayed retry, error class).
"""

from __future__ import annotations

import json
import time

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
API_BASE = "https://bbs-api.miyoushe.com"
REFERER = "https://bbs.miyoushe.com/"
REQUEST_DELAY_SECONDS = 2.0
RETRY_DELAY_SECONDS = 5.0


class MiyousheError(Exception):
    """Raised when the miyoushe API rejects a request."""


def post_text(content: object) -> str:
    """Extract the plain-text body from a post's JSON content string."""
    if not isinstance(content, str) or not content.strip():
        return ""
    try:
        payload = json.loads(content)
    except ValueError:
        return content.strip()
    if isinstance(payload, dict):
        return str(payload.get("describe") or "").strip()
    return ""


class MiyousheClient:
    def __init__(self, timeout: int = 20, delay: float = REQUEST_DELAY_SECONDS):
        self.timeout = timeout
        self.delay = delay
        self._last_request = 0.0
        self._client = httpx.Client(headers={"User-Agent": USER_AGENT, "Referer": REFERER})

    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def forum_posts(self, gids: int, forum_id: int, limit: int = 20) -> list[dict]:
        """Recent posts of a forum board, sorted by latest activity."""
        url = (
            f"{API_BASE}/post/wapi/getForumPostList"
            f"?gids={gids}&forum_id={forum_id}&page_size={min(limit, 50)}&sort_type=1"
        )
        for attempt in range(2):
            self._throttle()
            try:
                response = self._client.get(url, timeout=self.timeout)
            except (httpx.HTTPError, OSError) as error:
                if attempt == 0:
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                raise MiyousheError(f"Request failed for {url}: {error}") from error
            try:
                payload = response.json()
            except ValueError as error:
                raise MiyousheError(f"Malformed JSON from {url}: {error}") from error
            if payload.get("retcode") != 0:
                raise MiyousheError(f"retcode {payload.get('retcode')} from {url}")
            posts = []
            for item in (payload.get("data") or {}).get("list") or []:
                post = item.get("post") or {}
                stat = item.get("stat") or {}
                post_id = post.get("post_id")
                subject = str(post.get("subject") or "").strip()
                if not post_id:
                    continue
                posts.append(
                    {
                        "post_id": str(post_id),
                        "title": subject,
                        "text": post_text(post.get("content"))[:1000],
                        "uid": str(post.get("uid") or ""),
                        "created_at": int(post.get("created_at") or 0) or None,
                        "reply_num": int(stat.get("reply_num") or 0),
                        "like_num": int(stat.get("like_num") or 0),
                        "view_num": int(stat.get("view_num") or 0),
                    }
                )
            return posts[:limit]
        raise MiyousheError(f"Request failed for {url}")  # pragma: no cover
