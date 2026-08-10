"""Tieba collector: forum threads and their reply floors.

Uses the Tieba client API (c.tieba.baidu.com) with the client-side sign scheme:
parameters sorted by key, joined as ``k=v``, suffixed with ``tiebaclient!!!``,
then MD5-hashed into the ``sign`` field. Anonymous access works with the public
client identity parameters; the web frontend (tieba.baidu.com) is gated behind
a 403 safety check and is not used. Style mirrors collectors/bilibili.py
(stdlib urllib only, throttled requests, one delayed retry).
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "bdtb for Android 12.55.1.0"
SIGN_SUFFIX = "tiebaclient!!!"
CLIENT_TYPE = "2"
CLIENT_VERSION = "12.55.1.0"
API_BASE = "https://c.tieba.baidu.com"
REQUEST_DELAY_SECONDS = 2.0
RETRY_DELAY_SECONDS = 5.0


class TiebaError(Exception):
    """Raised when the Tieba client API rejects a request."""


def sign_params(params: dict) -> dict:
    """Return params with the client ``sign`` field added."""
    signed = {key: str(value) for key, value in params.items()}
    payload = "".join(f"{key}={signed[key]}" for key in sorted(signed)) + SIGN_SUFFIX
    signed["sign"] = hashlib.md5(payload.encode()).hexdigest()
    return signed


def _client_id() -> str:
    return f"wappc_{random.randrange(10**12, 10**13)}_{random.randrange(100, 1000)}"


def _post_text(content: object) -> str:
    """pb/page post content is a list of fragments; text fragments carry ``text``."""
    if not isinstance(content, list):
        return ""
    parts = [item.get("text", "") for item in content if isinstance(item, dict)]
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _author_name(author: object) -> str:
    if not isinstance(author, dict):
        return ""
    return author.get("name_show") or author.get("name") or ""


def _user_map(payload: dict) -> dict[str, dict]:
    """Anonymous responses carry no embedded author object; user details live in
    a top-level ``user_list`` keyed by the post/thread ``author_id``."""
    return {str(user.get("id")): user for user in payload.get("user_list") or []}


def _agree_count(post: dict) -> int:
    agree = post.get("agree")
    if isinstance(agree, dict):
        return int(agree.get("agree_num") or 0)
    return int(agree or post.get("agree_num") or 0)


class TiebaClient:
    def __init__(self, timeout: int = 20, delay: float = REQUEST_DELAY_SECONDS):
        self.timeout = timeout
        self.delay = delay
        self._last_request = 0.0
        self._common = {
            "_client_id": _client_id(),
            "_client_type": CLIENT_TYPE,
            "_client_version": CLIENT_VERSION,
        }
        self._opener = urllib.request.build_opener()
        self._opener.addheaders = [("User-Agent", USER_AGENT)]

    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _post_json(self, path: str, params: dict) -> dict:
        """Signed form POST; one delayed retry on transport or API errors."""
        body = urllib.parse.urlencode(sign_params({**self._common, **params})).encode()
        url = API_BASE + path
        request = urllib.request.Request(url, data=body)
        for attempt in range(2):
            self._throttle()
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, OSError, ValueError) as error:
                if attempt == 0:
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                raise TiebaError(f"Request failed for {url}: {error}") from error
            code = int(payload.get("error_code") or 0)
            if code == 0:
                return payload
            if attempt == 0:
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            raise TiebaError(f"API error_code {code}: {payload.get('error_msg')} for {url}")
        raise TiebaError(f"Request failed for {url}")  # pragma: no cover

    def forum_threads(self, kw: str, limit: int = 20) -> list[dict]:
        """Recent threads of a forum (吧名不带"吧"字)."""
        payload = self._post_json("/c/f/frs/page", {"kw": kw, "pn": 0, "rn": min(limit, 30)})
        users = _user_map(payload)
        threads = []
        for item in payload.get("thread_list") or []:
            threads.append(
                {
                    "tid": str(item.get("id")),
                    "title": item.get("title") or "",
                    "reply_num": int(item.get("reply_num") or 0),
                    "author": _author_name(users.get(str(item.get("author_id")))),
                    "create_time": int(item.get("create_time") or 0) or None,
                }
            )
        return threads[:limit]

    def thread_posts(self, tid: str, limit: int = 30) -> list[dict]:
        """Floors of a thread; floor 1 is the opening post. Paginates until
        ``limit`` reply floors are collected (the server caps a page at 30
        posts including floor 1, so a full reply page needs a second request).

        Note the thread id parameter is ``kz`` (``tid`` is rejected with 350004).
        """
        posts: list[dict] = []
        for page in (1, 2):
            payload = self._post_json("/c/f/pb/page", {"kz": tid, "pn": page, "rn": 30})
            users = _user_map(payload)
            batch = payload.get("post_list") or []
            for post in batch:
                author = users.get(str(post.get("author_id"))) or {}
                text = _post_text(post.get("content"))
                if not text:
                    continue
                posts.append(
                    {
                        "post_id": str(post.get("id") or ""),
                        "floor": int(post.get("floor") or 0),
                        "author": _author_name(author),
                        "user_key": str(author.get("portrait") or _author_name(author)),
                        "text": text[:500],
                        "like": _agree_count(post),
                        "time": int(post.get("time") or 0) or None,
                    }
                )
            replies = sum(1 for post in posts if post["floor"] > 1)
            if len(batch) < 30 or replies >= limit:
                break
        return posts[: limit + 1]


def format_replies_section(replies: list[dict]) -> str:
    if not replies:
        return ""
    lines = ["热门回复:"]
    for index, item in enumerate(replies, start=1):
        lines.append(f"{index}. {item['message']} (赞{item['like']})")
    return "\n".join(lines)
