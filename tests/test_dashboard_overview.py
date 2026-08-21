# tests/test_dashboard_overview.py
from datetime import datetime, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from dashboard import config
from dashboard.app import create_app
from dashboard.auth import session_value
from dashboard.stats import overview_stats

CREATOR = 123456789


def _bot():
    b = MagicMock()
    b.latency = 0.042
    g = MagicMock()
    g.member_count = 10
    g.name = "Test"
    g.id = 1
    g.voice_clients = []
    b.guilds = [g]
    b.start_time = datetime.now(timezone.utc)
    b.extensions = {"cogs.music": None}
    b.cogs = {"Music": object()}
    return b


def test_overview_stats_shape():
    s = overview_stats(_bot())
    assert s["latency_ms"] == 42
    assert s["guild_count"] == 1
    assert s["member_total"] == 10
    assert s["cogs"][0]["name"] == "Music"
    assert s["cogs"][0]["loaded"] is True


def test_overview_page_renders():
    c = TestClient(create_app(bot=_bot()))
    c.cookies.set(config.SESSION_COOKIE, session_value(CREATOR))
    html = c.get("/").text
    assert "Overview" in html and "42" in html


def test_overview_requires_auth():
    r = TestClient(create_app(bot=_bot()), follow_redirects=False).get("/")
    assert r.status_code == 303
