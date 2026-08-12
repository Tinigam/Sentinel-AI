# Login Session Management

Some collection targets (NGA `bbs.nga.cn`, Weibo `s.weibo.com`/`weibo.com`) reject anonymous access, and Bilibili risk control intermittently blocks anonymous video lists. These sources authenticate with a browser session that is captured once on the host machine, persisted to disk, and reused until it expires. Collection volume is low, so a single stored session per site is sufficient — no rotation or pooling.

Session files contain live cookies and are treated as secrets: `sessions/` is gitignored and must never be committed, like `.env`.

## Logging In

Run the login script on the host, never inside a container:

```text
pip install -r scripts/requirements-login.txt
playwright install chromium
python scripts/login.py --site weibo|nga|bilibili
```

A headed Chromium window opens on the site's login page. Scan the QR code or log in manually. The script polls `context.cookies()` for the site's key cookie (Weibo `SUB`, NGA `ngaPassportCid`, Bilibili `SESSDATA`) and times out after 300 seconds. On success it writes `sessions/<site>.json` in Playwright `storage_state` format and prints the saved path plus the key cookie's expiry time.

## How the Backend Uses Sessions

- `docker-compose.yml` mounts `./sessions` read-only at `/app/sessions`; `Settings.sessions_dir` points there.
- `app/services/session_auth.py` keeps the site registry (`SITES`): key cookie, cookie-domain filter, and probe function per site.
- `load_cookie_header(site)` reads the storage_state file, keeps only cookies whose domain belongs to the site, and returns a `Cookie` header string; it returns `None` when the file is missing or malformed. Cookies are deduplicated by name — when the same cookie exists on several domains (Weibo's `SUB` on both `.weibo.com` and `.weibo.cn`), the one on the more specific domain wins.
- `check_session(site)` runs a lightweight authenticated probe and returns `{site, status, detail}` with `status` in `ok` / `expired` / `missing`. Probes: Weibo `GET https://s.weibo.com/weibo?q=test` (HTTP 200 with `feed_list_item` result cards in the page vs login redirect / no cards), NGA `GET https://bbs.nga.cn/nuke.php?__output=8&func=ucp` (HTTP 200 vs 403/redirect), Bilibili `GET https://api.bilibili.com/x/web-interface/nav` (`data.isLogin`). All probe exceptions are swallowed and reported as `expired`.

The Weibo collector searches the desktop web (`s.weibo.com/weibo?q=<keyword>`) with this session. The `m.weibo.cn` mobile API returns empty results for this session and is deliberately not used.

`GET /api/v1/sources/sessions` returns the `check_session` result for every registered site. It is read-only and does not require the ingest key.

## When a Session Expires

1. `GET /api/v1/sources/sessions` reports `expired` or `missing` for the site (check after ingestion anomalies too).
2. Re-run `python scripts/login.py --site <site>` on the host; the new file overwrites the old one.
3. No container restart is required for the file itself, but the running backend only picks up the `./sessions` mount after the compose change is applied (the volume is read-only; new files on the host are visible immediately once mounted).

The NGA and Weibo collectors consume their stored sessions directly; the stored Bilibili session supplements the existing `BILIBILI_COOKIE` env var as the login fallback, and the env var remains supported.
