import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import session_auth
from app.services.session_auth import check_session, load_cookie_header


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        session_auth, "get_settings", lambda: SimpleNamespace(sessions_dir=tmp_path)
    )
    return tmp_path


def write_state(sessions_dir, site: str, cookies: list[dict]) -> None:
    state = {"cookies": cookies, "origins": []}
    (sessions_dir / f"{site}.json").write_text(json.dumps(state), encoding="utf-8")


def fake_cookie(name: str, domain: str, value: str = "x") -> dict:
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": "/",
        "expires": 2000000000,
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax",
    }


def test_load_cookie_header_filters_by_domain(sessions_dir) -> None:
    write_state(sessions_dir, "weibo", [
        fake_cookie("SUB", ".weibo.cn", "keep"),
        fake_cookie("MLOGIN", "m.weibo.cn", "keep2"),
        fake_cookie("SUBP", ".weibo.com", "keep3"),
        fake_cookie("other", ".example.com", "drop"),
        fake_cookie("evil", "notweibo.cn", "drop2"),
    ])

    header = load_cookie_header("weibo")

    assert header == "SUB=keep; MLOGIN=keep2; SUBP=keep3"


def test_load_cookie_header_missing_file_returns_none(sessions_dir) -> None:
    assert load_cookie_header("weibo") is None


def test_load_cookie_header_dedupes_by_name_keeping_specific_domain(sessions_dir) -> None:
    write_state(sessions_dir, "weibo", [
        fake_cookie("SUB", ".weibo.cn", "cn-value"),
        fake_cookie("SUB", ".weibo.com", "com-value"),
        fake_cookie("MLOGIN", "m.weibo.cn", "keep"),
    ])

    header = load_cookie_header("weibo")

    assert header == "SUB=com-value; MLOGIN=keep"


def test_load_cookie_header_malformed_file_returns_none(sessions_dir) -> None:
    (sessions_dir / "nga.json").write_text("not json", encoding="utf-8")
    assert load_cookie_header("nga") is None
    write_state(sessions_dir, "nga", [])
    assert load_cookie_header("nga") is None


def test_load_cookie_header_unknown_site_returns_none(sessions_dir) -> None:
    assert load_cookie_header("twitter") is None


def fake_response(payload: dict, status_code: int = 200, text: str = "") -> SimpleNamespace:
    return SimpleNamespace(status_code=status_code, json=lambda: payload, text=text)


def mock_get(monkeypatch, responder) -> None:
    monkeypatch.setattr(httpx, "get", responder)


def test_check_session_missing_file(sessions_dir) -> None:
    result = check_session("weibo")
    assert result["site"] == "weibo"
    assert result["status"] == "missing"


def test_check_session_bilibili_ok(sessions_dir, monkeypatch) -> None:
    write_state(sessions_dir, "bilibili", [fake_cookie("SESSDATA", ".bilibili.com")])
    mock_get(monkeypatch, lambda *a, **k: fake_response({"data": {"isLogin": True}}))

    result = check_session("bilibili")

    assert result == {
        "site": "bilibili",
        "status": "ok",
        "detail": "bilibili nav reports isLogin=true",
    }


def test_check_session_bilibili_expired(sessions_dir, monkeypatch) -> None:
    write_state(sessions_dir, "bilibili", [fake_cookie("SESSDATA", ".bilibili.com")])
    mock_get(monkeypatch, lambda *a, **k: fake_response({"data": {"isLogin": False}}))

    assert check_session("bilibili")["status"] == "expired"


def test_check_session_weibo_ok(sessions_dir, monkeypatch) -> None:
    write_state(sessions_dir, "weibo", [fake_cookie("SUB", ".weibo.com")])
    mock_get(monkeypatch, lambda *a, **k: fake_response({}, text='<div action-type="feed_list_item">'))

    result = check_session("weibo")

    assert result["status"] == "ok"


def test_check_session_weibo_expired_without_cards(sessions_dir, monkeypatch) -> None:
    write_state(sessions_dir, "weibo", [fake_cookie("SUB", ".weibo.com")])
    mock_get(monkeypatch, lambda *a, **k: fake_response({}, text="<html>login</html>"))
    assert check_session("weibo")["status"] == "expired"
    mock_get(monkeypatch, lambda *a, **k: fake_response({}, status_code=302))
    assert check_session("weibo")["status"] == "expired"


def test_check_session_nga_status_code_branches(sessions_dir, monkeypatch) -> None:
    write_state(sessions_dir, "nga", [fake_cookie("ngaPassportCid", ".nga.cn")])
    mock_get(monkeypatch, lambda *a, **k: fake_response({}, status_code=200))
    assert check_session("nga")["status"] == "ok"
    mock_get(monkeypatch, lambda *a, **k: fake_response({}, status_code=403))
    assert check_session("nga")["status"] == "expired"


def test_check_session_network_error_counts_as_expired(sessions_dir, monkeypatch) -> None:
    write_state(sessions_dir, "weibo", [fake_cookie("SUB", ".weibo.cn")])

    def raise_timeout(*args, **kwargs):
        raise httpx.ConnectTimeout("boom")

    mock_get(monkeypatch, raise_timeout)

    result = check_session("weibo")
    assert result["status"] == "expired"
    assert "boom" in result["detail"]


def test_check_session_unknown_site(sessions_dir) -> None:
    assert check_session("twitter")["status"] == "missing"
