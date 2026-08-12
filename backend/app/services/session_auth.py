"""Load and probe Playwright login sessions for authenticated sources.

`sessions/<site>.json` files are produced by `scripts/login.py` (Playwright
storage_state format) and mounted read-only into the backend container at
`/app/sessions`. Each file replaces a hand-maintained cookie env var.
"""

import json
from collections.abc import Callable

import httpx

from app.core.config import get_settings

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
PROBE_TIMEOUT_SECONDS = 10


def _probe_weibo(cookie_header: str) -> dict:
    response = httpx.get(
        "https://s.weibo.com/weibo?q=test",
        headers={
            "Cookie": cookie_header,
            "User-Agent": USER_AGENT,
            "Referer": "https://s.weibo.com/",
        },
        timeout=PROBE_TIMEOUT_SECONDS,
        follow_redirects=False,
    )
    if response.status_code == 200 and "feed_list_item" in response.text:
        return {"status": "ok", "detail": "s.weibo.com search returns result cards"}
    if response.status_code == 200:
        return {
            "status": "expired",
            "detail": "s.weibo.com search returned no result cards (anonymous session)",
        }
    return {
        "status": "expired",
        "detail": f"s.weibo.com returned HTTP {response.status_code} (login required)",
    }


def _probe_nga(cookie_header: str) -> dict:
    response = httpx.get(
        "https://bbs.nga.cn/nuke.php?__output=8&func=ucp",
        headers={"Cookie": cookie_header, "User-Agent": USER_AGENT},
        timeout=PROBE_TIMEOUT_SECONDS,
        follow_redirects=False,
    )
    if response.status_code == 200:
        return {"status": "ok", "detail": "bbs.nga.cn user centre reachable"}
    return {
        "status": "expired",
        "detail": f"bbs.nga.cn returned HTTP {response.status_code} (login required)",
    }


def _probe_bilibili(cookie_header: str) -> dict:
    response = httpx.get(
        "https://api.bilibili.com/x/web-interface/nav",
        headers={"Cookie": cookie_header, "User-Agent": USER_AGENT},
        timeout=PROBE_TIMEOUT_SECONDS,
    )
    data = response.json().get("data") or {}
    if data.get("isLogin"):
        return {"status": "ok", "detail": "bilibili nav reports isLogin=true"}
    return {"status": "expired", "detail": "bilibili nav reports an anonymous session"}


# Registered login-required sites. `cookie_domains` filters storage_state
# cookies by suffix; `key_cookie` is what scripts/login.py waits for; `probe`
# is a lightweight authenticated request that decides ok vs expired.
SITES: dict[str, dict] = {
    "weibo": {
        "key_cookie": "SUB",
        "cookie_domains": ["weibo.cn", "weibo.com"],
        "probe": _probe_weibo,
    },
    "nga": {
        "key_cookie": "ngaPassportCid",
        "cookie_domains": ["nga.cn", "ngabbs.com", "nga.178.com"],
        "probe": _probe_nga,
    },
    "bilibili": {
        "key_cookie": "SESSDATA",
        "cookie_domains": ["bilibili.com"],
        "probe": _probe_bilibili,
    },
}


def _matches_domain(cookie_domain: str, allowed: list[str]) -> bool:
    domain = cookie_domain.lstrip(".").lower()
    return any(domain == root or domain.endswith("." + root) for root in allowed)


def load_cookie_header(site: str) -> str | None:
    """Build a Cookie header for `site` from its storage_state file, or None.

    Cookies are deduplicated by name: when the same cookie exists on several
    domains (for example Weibo's SUB on both .weibo.com and .weibo.cn), the one
    on the more specific (longer) domain wins.
    """
    if site not in SITES:
        return None
    path = get_settings().sessions_dir / f"{site}.json"
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        cookies = state["cookies"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    allowed = SITES[site]["cookie_domains"]
    best: dict[str, tuple[str, int]] = {}
    for cookie in cookies:
        domain = str(cookie.get("domain", ""))
        if not _matches_domain(domain, allowed):
            continue
        name = str(cookie.get("name", ""))
        specificity = len(domain.lstrip("."))
        if name not in best or specificity > best[name][1]:
            best[name] = (str(cookie.get("value", "")), specificity)
    if not best:
        return None
    return "; ".join(f"{name}={value}" for name, (value, _) in best.items())


def check_session(site: str) -> dict:
    """Probe whether the stored session for `site` is still authenticated."""
    if site not in SITES:
        return {"site": site, "status": "missing", "detail": "unregistered site"}
    cookie_header = load_cookie_header(site)
    if cookie_header is None:
        path = get_settings().sessions_dir / f"{site}.json"
        return {
            "site": site,
            "status": "missing",
            "detail": f"no usable session file at {path}; run scripts/login.py --site {site}",
        }
    probe: Callable[[str], dict] = SITES[site]["probe"]
    try:
        result = probe(cookie_header)
    except Exception as exc:  # noqa: BLE001  # network/parse failures look like expiry
        result = {"status": "expired", "detail": f"probe failed: {type(exc).__name__}: {exc}"}
    return {"site": site, **result}
