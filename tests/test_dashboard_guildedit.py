# tests/test_dashboard_guildedit.py
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from dashboard import config
from dashboard.app import create_app
from dashboard.auth import csrf_token, session_value
from utils import load_json, save_json

CREATOR = 123456789


def _client(tmp_path, monkeypatch):
    cfg_file = str(tmp_path / "config.json")
    monkeypatch.setattr("dashboard.app.CONFIG_FILE", cfg_file)
    save_json(cfg_file, {"555": {"whitelist": ["111"], "ai_channels": ["999"]}})
    b = MagicMock()
    g = MagicMock()
    g.name = "Seoulities"
    g.id = 555
    g.member_count = 5
    g.icon = None
    g.channels = []
    g.roles = []
    b.guilds = [g]
    b.get_guild.return_value = g
    ch = MagicMock()
    ch.name = "general"
    b.get_channel.return_value = ch
    c = TestClient(create_app(bot=b), follow_redirects=False)
    c.cookies.set(config.SESSION_COOKIE, session_value(CREATOR))
    return c, cfg_file


def test_whitelist_add_and_del(tmp_path, monkeypatch):
    c, cfg_file = _client(tmp_path, monkeypatch)
    r = c.post(
        "/guilds/555/whitelist/add",
        data={"value": "222", "csrf": csrf_token(CREATOR)},
    )
    assert r.status_code == 303
    assert sorted(load_json(cfg_file)["555"]["whitelist"]) == ["111", "222"]

    r = c.post(
        "/guilds/555/whitelist/del",
        data={"value": "111", "csrf": csrf_token(CREATOR)},
    )
    assert r.status_code == 303
    assert load_json(cfg_file)["555"]["whitelist"] == ["222"]


def test_ai_channels_add_del_and_names(tmp_path, monkeypatch):
    c, cfg_file = _client(tmp_path, monkeypatch)
    html = c.get("/guilds/555").text
    assert "#general" in html
    r = c.post(
        "/guilds/555/ai_channels/add",
        data={"value": "777", "csrf": csrf_token(CREATOR)},
    )
    assert r.status_code == 303
    assert load_json(cfg_file)["555"]["ai_channels"] == ["999", "777"]
    r = c.post(
        "/guilds/555/ai_channels/del",
        data={"value": "999", "csrf": csrf_token(CREATOR)},
    )
    assert load_json(cfg_file)["555"]["ai_channels"] == ["777"]


def test_rejects_non_numeric_and_bad_key(tmp_path, monkeypatch):
    c, cfg_file = _client(tmp_path, monkeypatch)
    r = c.post(
        "/guilds/555/whitelist/add",
        data={"value": "not-a-number", "csrf": csrf_token(CREATOR)},
    )
    assert r.status_code == 303  # redirect with FAIL msg, list untouched
    assert load_json(cfg_file)["555"]["whitelist"] == ["111"]

    r = c.post(
        "/guilds/555/boguskey/add",
        data={"value": "123", "csrf": csrf_token(CREATOR)},
    )
    assert r.status_code == 400

    r = c.post(
        "/guilds/555/whitelist/add",
        data={"value": "123", "csrf": "bad"},
    )
    assert r.status_code == 403
