from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[tuple[str, str]] = []
        self.href = ""
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.href = dict(attrs).get("href") or ""
            self.text = []

    def handle_data(self, data: str) -> None:
        if self.href:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.href:
            self.items.append((self.href, re.sub(r"\s+", " ", " ".join(self.text)).strip()))
            self.href = ""
            self.text = []


def fetch_html(url: str, timeout: int = 20) -> str:
    request = Request(url, headers={"User-Agent": "Sentinel-AI/0.1 (+public-announcement-monitor)"})
    with urlopen(request, timeout=timeout) as response:  # nosec B310
        return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def discover_announcements(list_url: str, limit: int = 20) -> list[dict[str, str]]:
    parser = _Links()
    parser.feed(fetch_html(list_url))
    origin = urlparse(list_url).netloc
    seen: set[str] = set()
    results = []
    for href, title in parser.items:
        url = urljoin(list_url, href)
        if urlparse(url).netloc != origin or not title or url in seen:
            continue
        if not re.search(r"news|article|detail|announcement|content", url, re.I):
            continue
        seen.add(url)
        results.append({"title": unescape(title), "url": url})
        if len(results) >= limit:
            break
    return results