"""Weibo collector: desktop web search result cards.

Uses the s.weibo.com search page with a logged-in session cookie (the
m.weibo.cn mobile API returns empty results for this session and is not used).
Each result card (``div[action-type=feed_list_item]``) becomes one post; only
the card page is fetched, so no per-post comments are collected. Style mirrors
collectors/tieba.py (throttled requests, one delayed retry, error class).
"""

from __future__ import annotations

import re
import time
import urllib.parse
from datetime import UTC, datetime, timedelta

import httpx
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
SEARCH_URL = "https://s.weibo.com/weibo"
REQUEST_DELAY_SECONDS = 2.0
RETRY_DELAY_SECONDS = 5.0

_NUMBER_RE = re.compile(r"\d+")


class WeiboError(Exception):
    """Raised when a Weibo search request fails or the session is rejected."""


def parse_weibo_time(text: str, now: datetime | None = None) -> datetime | None:
    """Best-effort parse of the relative timestamps shown on result cards."""
    now = now or datetime.now(UTC)
    text = text.strip()
    match = re.search(r"(\d+)\s*分钟前", text)
    if match:
        return now - timedelta(minutes=int(match.group(1)))
    match = re.search(r"今天\s*(\d{1,2}):(\d{2})", text)
    if match:
        return now.replace(
            hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0
        )
    match = re.search(r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})", text)
    if match:
        month, day, hour, minute = (int(match.group(i)) for i in range(1, 5))
        try:
            return now.replace(month=month, day=day, hour=hour, minute=minute,
                               second=0, microsecond=0)
        except ValueError:
            return None
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})\s*(\d{1,2}):(\d{2})", text)
    if match:
        try:
            return datetime(*(int(match.group(i)) for i in range(1, 6)), tzinfo=UTC)
        except ValueError:
            return None
    return None


def _act_count(items: list, index: int) -> int:
    if index >= len(items):
        return 0
    match = _NUMBER_RE.search(items[index].get_text(" ", strip=True))
    return int(match.group(0)) if match else 0


def parse_search_html(html_text: str, limit: int = 20) -> list[dict]:
    """Parse one s.weibo.com search page into post dicts."""
    soup = BeautifulSoup(html_text, "html.parser")
    posts = []
    for card in soup.select('div[action-type="feed_list_item"]'):
        mid = card.get("mid")
        # The hidden full-text node carries the untruncated body when present.
        full = card.select_one('p.txt[node-type="feed_list_content_full"]')
        visible = card.select_one('p.txt[node-type="feed_list_content"]') or card.select_one("p.txt")
        body_node = full or visible
        text = body_node.get_text(" ", strip=True) if body_node else ""
        author_node = card.select_one("a.name") or card.select_one("a.nick")
        author = author_node.get_text(strip=True) if author_node else ""
        from_node = card.select_one(".from a")
        url = ""
        if from_node and from_node.get("href"):
            href = str(from_node["href"])
            url = urllib.parse.urljoin("https://s.weibo.com/", href)
            url = url.split("?")[0]
        actions = card.select(".card-act ul li")
        if not mid or not text or not url:
            continue
        posts.append(
            {
                "mid": str(mid),
                "url": url,
                "author": author,
                "text": text[:1000],
                "repost": _act_count(actions, 0),
                "comment": _act_count(actions, 1),
                "like": _act_count(actions, 2),
                "published_at": parse_weibo_time(
                    from_node.get_text(" ", strip=True) if from_node else ""
                ),
            }
        )
        if len(posts) >= limit:
            break
    return posts


class WeiboClient:
    def __init__(self, cookie: str, timeout: int = 20, delay: float = REQUEST_DELAY_SECONDS):
        self.timeout = timeout
        self.delay = delay
        self._last_request = 0.0
        self._client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://s.weibo.com/",
                "Cookie": cookie,
            },
            follow_redirects=False,
        )

    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def search_posts(self, query: str, limit: int = 20) -> list[dict]:
        """One page of search results (~20 cards) for a keyword."""
        url = SEARCH_URL + "?" + urllib.parse.urlencode({"q": query})
        for attempt in range(2):
            self._throttle()
            try:
                response = self._client.get(url, timeout=self.timeout)
            except (httpx.HTTPError, OSError) as error:
                if attempt == 0:
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                raise WeiboError(f"Request failed for {url}: {error}") from error
            if response.status_code != 200:
                raise WeiboError(
                    f"HTTP {response.status_code} for {url} (session missing or expired)"
                )
            return parse_search_html(response.text, limit=limit)
        raise WeiboError(f"Request failed for {url}")  # pragma: no cover
