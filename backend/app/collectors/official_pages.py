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


_SKIP_TAGS = {"script", "style", "noscript", "template"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(document: str) -> str:
    parser = _Text()
    parser.feed(document)
    return re.sub(r"\s+", " ", unescape(" ".join(parser.parts))).strip()


def fetch_html(url: str, timeout: int = 20) -> str:
    request = Request(url, headers={"User-Agent": "Sentinel-AI/0.1 (+public-announcement-monitor)"})
    with urlopen(request, timeout=timeout) as response:  # nosec B310
        return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def discover_announcements(list_url: str, limit: int = 20) -> list[dict[str, str]]:
    document = fetch_html(list_url)
    parser = _Links()
    parser.feed(document)
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
    if results:
        return results
    # Fallback for SSR sites that embed links in JSON payloads instead of anchors.
    unescaped = document.replace("\\/", "/")
    embedded = set(re.findall(r"https?://[^\"'\s\\<>]+", unescaped))
    embedded |= {
        urljoin(list_url, match)
        for match in re.findall(r"[\"'](/[^\"'\s\\<>]+)[\"']", unescaped)
    }
    for url in sorted(embedded):
        url = url.split("#")[0]
        if urlparse(url).netloc != origin or url in seen:
            continue
        if not re.search(r"news|article|detail|announcement|content", url, re.I):
            continue
        seen.add(url)
        results.append({"title": "", "url": url})
        if len(results) >= limit:
            break
    return results


def extract_title(document: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", document, re.I | re.S)
    return re.sub(r"\s+", " ", unescape(match.group(1))).strip() if match else ""