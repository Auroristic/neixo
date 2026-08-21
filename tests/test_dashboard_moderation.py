# tests/test_dashboard_moderation.py
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from dashboard import config
from dashboard.app import create_app
from dashboard.auth import csrf_token, session_value
from utils import load_json, save_json

CREATOR = 123456789

WARNS = {"555": {"777": [{"reason": "spam", "timestamp": "2026-01-01T00:00:00+00:00"}]}}
CONFS = {
    "555_1": {
        "id": 1,
        "guild_id": "555",
        "text": "hi",
        "replies": [],
        "user_id": "9",
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
}


def _client(tmp_path, monkeypatch):
    wfile = str(tmp_path / "w.json")
    cfile = str(tmp_path / "c.json")
    monkeypatch.setattr("dashboard.app.WARNS_FILE", wfile)
    monkeypatch.setattr("dashboard.app.CONFESSIONS_FILE", cfile)
    save_json(wfile, WARNS)
    save_json(cfile, CONFS)
    c = TestClient(create_app(bot=MagicMock()), follow_redirects=False)
    c.cookies.set(config.SESSION_COOKIE, session_value(CREATOR))
    return c, wfile, cfile


def test_lists_render(tmp_path, monkeypatch):
    c, _, _ = _client(tmp_path, monkeypatch)
    html = c.get("/moderation").text
    assert "spam" in html and "hi" in html


def test_delete_warn(tmp_path, monkeypatch):
    c, wfile, _ = _client(tmp_path, monkeypatch)
    r = c.post(
        "/moderation/warns/delete",
        data={"guild_id": "555", "user_id": "777", "idx": "0", "csrf": csrf_token(CREATOR)},
    )
    assert r.status_code == 303
    assert load_json(wfile) == {"555": {"777": []}}


def test_delete_confession(tmp_path, monkeypatch):
    c, _, cfile = _client(tmp_path, monkeypatch)
    r = c.post(
        "/moderation/confessions/delete",
        data={"key": "555_1", "csrf": csrf_token(CREATOR)},
    )
    assert r.status_code == 303
    assert load_json(cfile) == {}


def test_delete_rejects_bad_csrf(tmp_path, monkeypatch):
    c, _, cfile = _client(tmp_path, monkeypatch)
    r = c.post("/moderation/confessions/delete", data={"key": "555_1", "csrf": "bad"})
    assert r.status_code == 403
    assert load_json(cfile) == CONFS
