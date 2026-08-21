# tests/test_dashboard_audit.py
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from dashboard import config
from dashboard.app import create_app
from dashboard.auth import session_value
from utils import log_audit

CREATOR = 123456789


def test_audit_page_shows_entries(tmp_path, monkeypatch):
    monkeypatch.setattr("dashboard.app.AUDIT_FILE", str(tmp_path / "a.json"))
    log_audit("dashboard.test", 0, CREATOR, "probe-entry")
    c = TestClient(create_app(bot=MagicMock()))
    c.cookies.set(config.SESSION_COOKIE, session_value(CREATOR))
    assert "probe-entry" in c.get("/audit").text


def test_audit_requires_auth(tmp_path, monkeypatch):
    monkeypatch.setattr("dashboard.app.AUDIT_FILE", str(tmp_path / "a.json"))
    r = TestClient(create_app(bot=MagicMock()), follow_redirects=False).get("/audit")
    assert r.status_code == 303
