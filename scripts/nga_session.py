"""Build an NGA session file from the manually provided NGA_COOKIE env var.

NGA's own login page sits behind a JS anti-bot interstitial that blocks fresh
browsers, so scripts/login.py cannot log in there directly. This script instead
injects the passport cookies (copied once from the user's daily browser into
NGA_COOKIE in .env) into a headed Chromium, lets the real browser solve the JS
challenge, and then persists the full cookie set to sessions/nga.json.

Usage (host only):

    python scripts/nga_session.py
"""

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = REPO_ROOT / "sessions"
TARGET = SESSIONS_DIR / "nga.json"
CHECK_URL = "https://bbs.nga.cn/thread.php?fid=-34587507"
GUEST_MARKER = "访客不能直接访问"
TIMEOUT_SECONDS = 120


def parse_cookie_header(header: str) -> list[dict]:
    cookies = []
    for pair in header.split(";"):
        name, _, value = pair.strip().partition("=")
        if name and value:
            cookies.append({"name": name, "value": value, "domain": ".nga.cn", "path": "/"})
    return cookies


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    header = os.environ.get("NGA_COOKIE", "")
    cookies = parse_cookie_header(header)
    if not any(c["name"] == "ngaPassportCid" for c in cookies):
        print("NGA_COOKIE missing ngaPassportCid; check .env", file=sys.stderr)
        return 1

    SESSIONS_DIR.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
        )
        context.add_cookies(cookies)
        page = context.new_page()
        page.goto(CHECK_URL)
        print("Injected passport cookies; waiting for the JS challenge to clear …")
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                body = page.content()
            except Exception:  # page mid-navigation
                time.sleep(2)
                continue
            if GUEST_MARKER not in body and "thread" in body.lower():
                context.storage_state(path=str(TARGET))
                print(f"Session saved to {TARGET}")
                browser.close()
                return 0
            time.sleep(3)
        print("Guest block did not clear within timeout.", file=sys.stderr)
        browser.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())
