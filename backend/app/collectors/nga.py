"""NGA collector: board thread lists and reply floors.

Uses the legacy web JSON view (`__output=8`) of bbs.nga.cn, which requires a
logged-in session (anonymous visitors get HTTP 403 "访客不能直接访问").
Responses are GB18030-encoded JSON; the thread list payload may contain raw
control characters, so it is parsed with ``strict=False``. Style mirrors
collectors/tieba.py (throttled requests, one delayed retry, error class).
"""

from __future__ import annotations

import json
import re
import time

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
SITE_BASE = "https://bbs.nga.cn"
REQUEST_DELAY_SECONDS = 2.0
RETRY_DELAY_SECONDS = 5.0

_IMG_TAG_RE = re.compile(r"\[img\].*?\[/img\]", re.IGNORECASE | re.DOTALL)
_BB_CODE_RE = re.compile(r"\[/?[a-zA-Z][^\]]*\]")


class NgaError(Exception):
    """Raised when an NGA request fails or the session is rejected."""


def clean_bbcode(text: object) -> str:
    """Strip BBCode tags and collapse whitespace from a floor's content.

    Image tags are removed together with their payload; other tags keep their
    inner text.
    """
    if not isinstance(text, str):
        return ""
    text = _IMG_TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", _BB_CODE_RE.sub(" ", text)).strip()


class NgaClient:
    def __init__(self, cookie: str, timeout: int = 20, delay: float = REQUEST_DELAY_SECONDS):
        self.timeout = timeout
        self.delay = delay
        self._last_request = 0.0
        self._client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Referer": SITE_BASE + "/",
                "Cookie": cookie,
            }
        )

    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _get_json(self, url: str) -> dict:
        """GET the GB18030 JSON view; one delayed retry on transport errors."""
        for attempt in range(2):
            self._throttle()
            try:
                response = self._client.get(url, timeout=self.timeout)
            except (httpx.HTTPError, OSError) as error:
                if attempt == 0:
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                raise NgaError(f"Request failed for {url}: {error}") from error
            if response.status_code != 200:
                raise NgaError(
                    f"HTTP {response.status_code} for {url} (session missing or expired)"
                )
            try:
                # The payload embeds raw control characters; strict=False accepts them.
                payload = json.loads(response.content.decode("gb18030", errors="replace"), strict=False)
            except ValueError as error:
                raise NgaError(f"Malformed JSON from {url}: {error}") from error
            data = payload.get("data")
            if not isinstance(data, dict):
                raise NgaError(f"Unexpected payload shape from {url}")
            return data
        raise NgaError(f"Request failed for {url}")  # pragma: no cover

    def forum_threads(self, fid: int, limit: int = 20) -> list[dict]:
        """Recent threads of a board. ``data.__T`` is a dict keyed by row index."""
        data = self._get_json(f"{SITE_BASE}/thread.php?fid={fid}&__output=8")
        rows = data.get("__T") or {}
        items = list(rows.values()) if isinstance(rows, dict) else rows
        threads = []
        for item in items:
            if not isinstance(item, dict) or not item.get("tid"):
                continue
            threads.append(
                {
                    "tid": str(item["tid"]),
                    "title": str(item.get("subject") or ""),
                    "reply_num": int(item.get("replies") or 0),
                    "author": str(item.get("author") or ""),
                    "create_time": int(item.get("postdate") or 0) or None,
                }
            )
        return threads[:limit]

    def thread_posts(self, tid: str, limit: int = 20) -> list[dict]:
        """Floors of a thread's first page. Floor 0 (``lou``) is the opening post.

        A single read.php page carries at most ~20 floors, which is the
        low-cost comment sample stored for distortion analysis.
        """
        data = self._get_json(f"{SITE_BASE}/read.php?tid={tid}&__output=8")
        rows = data.get("__R") or {}
        items = list(rows.values()) if isinstance(rows, dict) else rows
        posts = []
        for item in sorted(
            (entry for entry in items if isinstance(entry, dict)),
            key=lambda entry: int(entry.get("lou") or 0),
        ):
            text = clean_bbcode(item.get("content"))
            if not text:
                continue
            posts.append(
                {
                    "post_id": str(item.get("pid") or ""),
                    "floor": int(item.get("lou") or 0),
                    "author": str(item.get("author") or item.get("authorid") or ""),
                    "text": text[:500],
                    # read.php floors carry an epoch `postdatetimestamp`; the
                    # `postdate` field there is a display string.
                    "time": int(item.get("postdatetimestamp") or 0) or None,
                }
            )
        return posts[: limit + 1]
