"""Log in once on the host machine and persist a Playwright storage state.

Setup (host only, never inside Docker):

    pip install -r scripts/requirements-login.txt
    playwright install chromium

Usage:

    python scripts/login.py --site weibo|nga|bilibili

A headed Chromium window opens on the site's login page. Scan the QR code or
log in manually; once the key cookie appears the session is written to
``sessions/<site>.json`` (gitignored) and picked up by the backend through the
``/app/sessions`` read-only volume.
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

SITES = {
    "weibo": {
        "login_url": "https://passport.weibo.com/sso/signin?entry=miniblog&source=miniblog",
        "key_cookies": ["SUB"],
        # passport.weibo.com sets .weibo.com cookies first; the m.weibo.cn API
        # needs SUB on .weibo.cn, which only appears after visiting the mobile
        # site and letting SSO propagate.
        "post_login_url": "https://m.weibo.cn/",
        "key_cookie_domain": "weibo.cn",
    },
    "nga": {
        "login_url": "https://bbs.nga.cn/nuke.php?func=login",
        "key_cookies": ["ngaPassportCid"],
    },
    "bilibili": {
        "login_url": "https://passport.bilibili.com/login",
        "key_cookies": ["SESSDATA"],
    },
}

TIMEOUT_SECONDS = 300
POLL_INTERVAL_SECONDS = 2
SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"


def format_expiry(expires: float) -> str:
    if expires is None or expires < 0:
        return "session cookie (expires with browser)"
    return datetime.fromtimestamp(expires).isoformat(timespec="seconds")


def wait_for_login(context, key_cookies: list[str], domain: str | None = None) -> list[dict]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        cookies = context.cookies()
        matched = {
            cookie["name"]
            for cookie in cookies
            if domain is None or cookie["domain"].endswith(domain)
        }
        if all(name in matched for name in key_cookies):
            return cookies
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(
        f"login not detected within {TIMEOUT_SECONDS}s; "
        f"missing cookies: {key_cookies}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True, choices=sorted(SITES))
    args = parser.parse_args()
    site = SITES[args.site]

    SESSIONS_DIR.mkdir(exist_ok=True)
    target = SESSIONS_DIR / f"{args.site}.json"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(site["login_url"])
        print(f"Opened {site['login_url']} — log in (QR scan or password).")
        print(f"Waiting up to {TIMEOUT_SECONDS}s for cookies: {site['key_cookies']}")
        try:
            cookies = wait_for_login(context, site["key_cookies"])
            if site.get("post_login_url"):
                page.goto(site["post_login_url"])
                cookies = wait_for_login(
                    context, site["key_cookies"], site.get("key_cookie_domain")
                )
        except (TimeoutError, KeyboardInterrupt) as exc:
            print(f"Aborted: {exc}", file=sys.stderr)
            browser.close()
            return 1
        context.storage_state(path=str(target))
        browser.close()

    print(f"Session saved to {target}")
    for cookie in cookies:
        if cookie["name"] in site["key_cookies"]:
            print(f"  {cookie['name']} expires: {format_expiry(cookie.get('expires'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
