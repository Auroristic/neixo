# tests/test_dashboard_auth.py
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from dashboard import config
from dashboard.app import create_app
from dashboard.auth import csrf_token, check_csrf, make_state, session_value, verify_state
from dashboard.security import RateLimiter

CREATOR = 123456789  # conftest sets CREATOR_ID env


def client():
    return TestClient(create_app(bot=None), follow_redirects=False)


def test_valid_session_passes_gate():
    c = client()
    c.cookies.set(config.SESSION_COOKIE, session_value(CREATOR))
    assert c.get("/whoami").json() == {"user_id": CREATOR}


def test_wrong_discord_id_gets_403():
    c = client()
    c.cookies.set(config.SESSION_COOKIE, session_value(999))
    assert c.get("/whoami").status_code == 403


def test_no_session_redirects():
    assert client().get("/whoami").status_code == 303


def test_garbage_cookie_redirects():
    c = client()
    c.cookies.set(config.SESSION_COOKIE, "garbage.sig")
    assert c.get("/whoami").status_code == 303


def test_root_redirects_without_session():
    r = client().get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_state_roundtrip():
    s = make_state()
    assert verify_state(s, s)
    assert not verify_state(s, "tampered")
    assert not verify_state("", s)


def test_csrf_roundtrip():
    tok = csrf_token(CREATOR)
    assert check_csrf(CREATOR, tok)
    assert not check_csrf(CREATOR, "nope")
    assert not check_csrf(424242, tok)


def test_login_rate_limited():
    rl = RateLimiter(max_events=2, per_seconds=60)
    assert rl.allow("ip1") and rl.allow("ip1") and not rl.allow("ip1")
    assert rl.allow("ip2")


def test_login_sets_state_cookie_and_redirects_to_discord():
    r = client().get("/login")
    assert r.status_code == 303
    assert "discord.com/oauth2/authorize" in r.headers["location"]
    assert "state=" in r.headers["location"]
    assert config.STATE_COOKIE_NAME in r.headers.get("set-cookie", "")


def test_callback_rejects_bad_state():
    c = client()
    r = c.get("/callback?code=x&state=tampered")
    assert r.status_code == 403


@patch("dashboard.auth.fetch_discord_user", new_callable=AsyncMock)
@patch("dashboard.auth.exchange_code", new_callable=AsyncMock)
def test_callback_creator_gets_session(mock_fetch, mock_exchange):
    mock_exchange.return_value = "tok"
    mock_fetch.return_value = {"id": str(CREATOR)}
    c = client()
    state = make_state()
    c.cookies.set(config.STATE_COOKIE_NAME, state)
    r = c.get(f"/callback?code=good&state={state}")
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert config.SESSION_COOKIE in r.headers.get("set-cookie", "")


@patch("dashboard.auth.fetch_discord_user", new_callable=AsyncMock)
@patch("dashboard.auth.exchange_code", new_callable=AsyncMock)
def test_callback_stranger_gets_403(mock_fetch, mock_exchange):
    mock_exchange.return_value = "tok"
    mock_fetch.return_value = {"id": "999"}
    c = client()
    state = make_state()
    c.cookies.set(config.STATE_COOKIE_NAME, state)
    assert c.get(f"/callback?code=good&state={state}").status_code == 403


@patch("dashboard.auth.fetch_discord_user", new_callable=AsyncMock)
@patch("dashboard.auth.exchange_code", new_callable=AsyncMock)
def test_callback_bad_code_gets_502(mock_fetch, mock_exchange):
    mock_exchange.return_value = None
    c = client()
    state = make_state()
    c.cookies.set(config.STATE_COOKIE_NAME, state)
    assert c.get(f"/callback?code=bad&state={state}").status_code == 502
