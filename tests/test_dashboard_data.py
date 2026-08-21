# tests/test_dashboard_data.py
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import utils
from dashboard import config
from dashboard.app import create_app
from dashboard.auth import csrf_token, session_value
from dashboard.data_access import leaderboard, set_xp

CREATOR = 123456789


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "DB_FILE", str(tmp_path / "t.db"))
    monkeypatch.setattr(utils, "_db_initialized", False)
    old_local = utils._local
    utils._local = type(old_local)()
    yield
    utils._local = old_local


def test_set_and_leaderboard(fresh_db):
    assert set_xp("1", "555", 500, 3)
    set_xp("2", "555", 900, 5)
    rows = leaderboard()
    assert rows[0]["user_id"] == "2" and rows[0]["xp"] == 900


def test_set_xp_missing_row_returns_false(fresh_db):
    assert not set_xp("ghost", "555", 1, 1)


def test_data_page_and_xp_post(fresh_db):
    set_xp("1", "555", 10, 0)
    c = TestClient(create_app(bot=MagicMock()), follow_redirects=False)
    c.cookies.set(config.SESSION_COOKIE, session_value(CREATOR))
    assert c.get("/data").status_code == 200
    r = c.post(
        "/data/xp",
        data={"user_id": "1", "guild_id": "555", "xp": "123", "level": "2",
              "csrf": csrf_token(CREATOR)},
    )
    assert r.status_code == 303
    assert leaderboard()[0]["xp"] == 123


def test_xp_post_rejects_bad_csrf(fresh_db):
    set_xp("1", "555", 10, 0)
    c = TestClient(create_app(bot=MagicMock()), follow_redirects=False)
    c.cookies.set(config.SESSION_COOKIE, session_value(CREATOR))
    r = c.post("/data/xp", data={"user_id": "1", "guild_id": "555",
                                 "xp": "999", "level": "9", "csrf": "bad"})
    assert r.status_code == 403
    assert leaderboard()[0]["xp"] == 10
