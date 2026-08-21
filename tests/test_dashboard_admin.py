# tests/test_dashboard_admin.py
import logging
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from dashboard import config
from dashboard.app import create_app
from dashboard.auth import csrf_token, session_value
from dashboard.logs import attach_log_ring, recent_logs

CREATOR = 123456789


def _bot():
    b = MagicMock()
    b.reload_extension = AsyncMock()
    b.load_extension = AsyncMock()
    b.unload_extension = AsyncMock()
    b.extensions = {"cogs.music": None}
    return b


def _client(bot):
    c = TestClient(create_app(bot=bot), follow_redirects=False)
    c.cookies.set(config.SESSION_COOKIE, session_value(CREATOR))
    return c


def test_admin_page_lists_cogs():
    assert "music" in _client(_bot()).get("/admin").text


def test_reload_cog_calls_bot():
    b = _bot()
    r = _client(b).post(
        "/admin/cogs",
        data={"ext": "cogs.music", "action": "reload", "csrf": csrf_token(CREATOR)},
    )
    assert r.status_code == 303
    b.reload_extension.assert_awaited_with("cogs.music")


def test_cog_op_rejects_bad_ext():
    r = _client(_bot()).post(
        "/admin/cogs",
        data={"ext": "../evil", "action": "reload", "csrf": csrf_token(CREATOR)},
    )
    assert r.status_code == 400


def test_cog_op_rejects_bad_csrf():
    b = _bot()
    r = _client(b).post(
        "/admin/cogs",
        data={"ext": "cogs.music", "action": "reload", "csrf": "bad"},
    )
    assert r.status_code == 403
    b.reload_extension.assert_not_awaited()


def test_restart_endpoint_redirects(monkeypatch):
    monkeypatch.setenv("DASHBOARD_RESTART_CMD", "true")
    r = _client(_bot()).post("/admin/restart", data={"csrf": csrf_token(CREATOR)})
    assert r.status_code == 303


def test_log_ring_captures():
    attach_log_ring()
    logging.getLogger("test.probe").error("hello-ring")
    assert any("hello-ring" in line for line in recent_logs(50))


def test_logs_json_requires_auth():
    r = TestClient(create_app(bot=_bot()), follow_redirects=False).get(
        "/admin/logs/recent"
    )
    assert r.status_code == 303
