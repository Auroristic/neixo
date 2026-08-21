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


# ── bot identity + RPC ──────────────────────────────────────────

def _rpc_env(tmp_path, monkeypatch):
    import json

    import cogs.profile as prof

    store = tmp_path / "rpc.json"

    def load():
        try:
            raw = json.loads(store.read_text())
        except Exception:
            raw = {}
        # mirror cogs.profile._load_rpc normalization
        return {
            "entries": list(raw.get("entries", [])),
            "interval": int(raw.get("interval", 5)),
        }

    def save(state):
        store.write_text(json.dumps(state))

    monkeypatch.setattr(prof, "_load_rpc", load)
    monkeypatch.setattr(prof, "_save_rpc", save)
    return prof.rpc


def _identity_bot():
    from unittest.mock import MagicMock as M

    b = _bot()
    b.fetch_user = AsyncMock(return_value=M(banner=None))
    b.user.name = "xo"
    b.user.display_avatar.url = "https://cdn.discordapp.com/a.png"
    b.user.edit = AsyncMock()
    return b


def test_admin_page_shows_identity_and_rpc(tmp_path, monkeypatch):
    mgr = _rpc_env(tmp_path, monkeypatch)
    mgr.add_entry({"type": "custom", "name": "vibing", "emoji": None})
    html = _client(_identity_bot()).get("/admin").text
    assert "vibing" in html and "Bot identity" in html and "xo" in html


def test_pfp_endpoint_updates_avatar(tmp_path, monkeypatch):
    async def fake_fetch(url, max_bytes=None):
        return b"png-bytes"

    monkeypatch.setattr("dashboard.media.fetch_image", fake_fetch)
    b = _identity_bot()
    r = _client(b).post(
        "/admin/pfp",
        data={"url": "https://x/y.png", "csrf": csrf_token(CREATOR)},
    )
    assert r.status_code == 303
    b.user.edit.assert_awaited_with(avatar=b"png-bytes")


def test_pfp_reset_clears_avatar(tmp_path, monkeypatch):
    b = _identity_bot()
    r = _client(b).post("/admin/pfp", data={"url": "reset", "csrf": csrf_token(CREATOR)})
    assert r.status_code == 303
    b.user.edit.assert_awaited_with(avatar=None)


def test_rpc_add_and_del(tmp_path, monkeypatch):
    mgr = _rpc_env(tmp_path, monkeypatch)
    c = _client(_identity_bot())
    r = c.post(
        "/admin/rpc/add",
        data={"text": "gaming", "emoji": "", "csrf": csrf_token(CREATOR)},
    )
    assert r.status_code == 303
    assert [e["name"] for e in mgr.list_entries()] == ["gaming"]
    r = c.post("/admin/rpc/del", data={"index": 1, "csrf": csrf_token(CREATOR)})
    assert r.status_code == 303
    assert mgr.list_entries() == []


def test_rpc_interval(tmp_path, monkeypatch):
    mgr = _rpc_env(tmp_path, monkeypatch)
    c = _client(_identity_bot())
    r = c.post("/admin/rpc/interval", data={"seconds": 30, "csrf": csrf_token(CREATOR)})
    assert r.status_code == 303
    assert mgr.get_interval() == 30
