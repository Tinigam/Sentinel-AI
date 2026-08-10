"""Bilibili collector: official account videos and their top comments.

Uses the public web API with WBI signing (documented at
https://github.com/SocialSisterYi/bilibili-API-collect). Anonymous access to the
account video list is frequently blocked by risk control from datacenter or
overseas IPs; providing a logged-in cookie via settings.bilibili_cookie makes
collection reliable. The comment endpoint is accessible anonymously.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MIXIN_KEY_TABLE = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]
REQUEST_DELAY_SECONDS = 2.0
RISK_CONTROL_RETRY_DELAY = 8.0


class BilibiliError(Exception):
    """Raised when the Bilibili API rejects a request (risk control or API error)."""


def mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[index] for index in MIXIN_KEY_TABLE)[:32]


def sign_params(params: dict, key: str, now: int | None = None) -> str:
    signed = dict(params)
    signed["wts"] = int(time.time()) if now is None else now
    query = urllib.parse.urlencode(sorted(signed.items()))
    signed["w_rid"] = hashlib.md5((query + key).encode()).hexdigest()
    return urllib.parse.urlencode(sorted(signed.items()))


class BilibiliClient:
    def __init__(self, cookie: str = "", timeout: int = 20, delay: float = REQUEST_DELAY_SECONDS):
        self.timeout = timeout
        self.delay = delay
        self._last_request = 0.0
        self._mixin: str | None = None
        jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        headers = [("User-Agent", USER_AGENT), ("Referer", "https://www.bilibili.com")]
        if cookie:
            headers.append(("Cookie", cookie))
        self._opener.addheaders = headers

    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _get_json(self, url: str, allow_codes: set[int] | None = None) -> dict:
        self._throttle()
        try:
            with self._opener.open(url, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            raise BilibiliError(f"HTTP {error.code} for {url}") from error
        code = payload.get("code")
        if code != 0 and code not in (allow_codes or set()):
            raise BilibiliError(f"API code {code}: {payload.get('message')} for {url}")
        return payload.get("data") or {}

    def bootstrap(self) -> None:
        """Obtain anonymous buvid cookies; tolerated failure (some endpoints work without)."""
        with contextlib.suppress(OSError):
            self._throttle()
            self._opener.open("https://www.bilibili.com", timeout=self.timeout).read(1)

    def _wbi_key(self) -> str:
        if self._mixin is None:
            # Anonymous nav returns code -101 (not logged in) but still carries wbi_img.
            data = self._get_json(
                "https://api.bilibili.com/x/web-interface/nav", allow_codes={-101}
            )
            wbi = data.get("wbi_img") or {}
            img_key = wbi.get("img_url", "").rsplit("/", 1)[-1].split(".")[0]
            sub_key = wbi.get("sub_url", "").rsplit("/", 1)[-1].split(".")[0]
            if not img_key or not sub_key:
                raise BilibiliError("Missing WBI keys in nav response")
            self._mixin = mixin_key(img_key, sub_key)
        return self._mixin

    def account_videos(self, mid: int, limit: int = 5) -> list[dict]:
        """Recent videos of an official account. Requires passing risk control
        (logged-in cookie recommended); one delayed retry on failure."""
        query = sign_params({"mid": mid, "ps": min(limit, 30), "pn": 1}, self._wbi_key())
        url = "https://api.bilibili.com/x/space/wbi/arc/search?" + query
        try:
            data = self._get_json(url)
        except BilibiliError:
            time.sleep(RISK_CONTROL_RETRY_DELAY)
            data = self._get_json(url)
        videos = (data.get("list") or {}).get("vlist") or []
        return [
            {
                "bvid": item["bvid"],
                "aid": item["aid"],
                "title": item["title"],
                "description": item.get("description") or "",
                "pubdate": item["created"],
            }
            for item in videos[:limit]
        ]

    def video_comments(self, aid: int, limit: int = 15) -> list[dict]:
        """Top (hot-sorted) top-level comments of a video. Anonymous access works.
        A single page of 20 hot comments covers any reasonable limit."""
        data = self._get_json(
            f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&ps=20&pn=1&sort=2"
        )
        comments: list[dict] = []
        for reply in data.get("replies") or []:
            message = re.sub(r"\s+", " ", (reply.get("content") or {}).get("message") or "").strip()
            if message:
                comments.append({"message": message[:200], "like": reply.get("like") or 0})
        comments.sort(key=lambda item: item["like"], reverse=True)
        return comments[:limit]

    def video_comments_full(self, aid: int, max_pages: int = 25) -> list[dict]:
        """Deep crawl of top-level comments, newest first when possible.
        Anonymous time-sorted access returns nothing; falls back to hot sort.
        Anonymous deep pagination is capped by the platform; stops on the first
        empty or rejected page and returns what was collected."""
        comments: list[dict] = []
        sort = 0
        for page in range(1, max_pages + 1):
            try:
                data = self._get_json(
                    f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&ps=20&pn={page}&sort={sort}"
                )
            except BilibiliError:
                break
            replies = data.get("replies") or []
            if not replies and page == 1 and sort == 0:
                sort = 2  # anonymous clients get no time-sorted replies
                continue
            if not replies:
                break
            for reply in replies:
                message = re.sub(
                    r"\s+", " ", (reply.get("content") or {}).get("message") or ""
                ).strip()
                if not message:
                    continue
                comments.append(
                    {
                        "comment_id": str(reply.get("rpid")),
                        "user_mid": str(reply.get("mid")),
                        "message": message[:500],
                        "like": reply.get("like") or 0,
                        "ctime": reply.get("ctime"),
                    }
                )
        return comments

    def search_official_account(self, keyword: str) -> dict | None:
        """Resolve an official account by game name via the all-in-one search."""
        query = sign_params({"keyword": keyword}, self._wbi_key())
        data = self._get_json("https://api.bilibili.com/x/web-interface/wbi/search/all/v2?" + query)
        groups = {g["result_type"]: g.get("data") or [] for g in data.get("result") or []}
        users = groups.get("bili_user") or []
        if not users:
            return None
        top = users[0]
        uname = re.sub(r"</?em[^>]*>", "", top.get("uname") or "")
        return {
            "mid": top["mid"],
            "uname": uname,
            "official": (top.get("official_verify") or {}).get("desc", ""),
        }


def format_comments_section(comments: list[dict]) -> str:
    if not comments:
        return ""
    lines = ["热门评论:"]
    for index, item in enumerate(comments, start=1):
        lines.append(f"{index}. {item['message']} (赞{item['like']})")
    return "\n".join(lines)
