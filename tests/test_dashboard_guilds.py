# tests/test_dashboard_guilds.py
from datetime import datetime, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from dashboard import config
from dashboard.app import create_app
from dashboard.auth import session_value

CREATOR = 123456789


def _bot():
    b = MagicMock()
    g = MagicMock()
    g.name = "Seoulities"
    g.id = 555
    g.member_count = 42
    g.icon.url = "https://cdn.discordapp.com/x.png"
    g.owner_id = 111
    g.chunked = True
    g.channels = [MagicMock(), MagicMock()]
    g.roles = [MagicMock()]
    g.me.joined_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    b.guilds = [g]
    b.get_guild.return_value = g
    return b


def test_guild_list_and_detail():
    c = TestClient(create_app(bot=_bot()))
    c.cookies.set(config.SESSION_COOKIE, session_value(CREATOR))
    lst = c.get("/guilds")
    assert lst.status_code == 200 and "Seoulities" in lst.text
    det = c.get("/guilds/555")
    assert det.status_code == 200 and "42" in det.text


def test_unknown_guild_404():
    b = _bot()
    b.get_guild.return_value = None
    c = TestClient(create_app(bot=b))
    c.cookies.set(config.SESSION_COOKIE, session_value(CREATOR))
    assert c.get("/guilds/12345").status_code == 404
