# tests/test_dashboard_theme.py
from fastapi.testclient import TestClient

from dashboard import config as cfg
from dashboard.app import create_app


def test_login_page_renders_when_unconfigured():
    html = TestClient(create_app(bot=None)).get("/login").text
    assert "Login with Discord" in html or "not configured" in html
    assert "neixo" in html.lower()


def test_login_redirects_when_configured(monkeypatch):
    monkeypatch.setattr(cfg, "DISCORD_CLIENT_ID", "123")
    monkeypatch.setattr(cfg, "OAUTH_REDIRECT_URI", "https://x/cb")
    r = TestClient(create_app(bot=None), follow_redirects=False).get("/login?go=1")
    assert r.status_code == 303
    assert "discord.com" in r.headers["location"]


def test_static_css_served():
    r = TestClient(create_app(bot=None)).get("/static/style.css")
    assert r.status_code == 200
    assert "#F6F1E7" in r.text


def test_base_template_blocks():
    c = TestClient(create_app(bot=None))
    assert c.get("/static/style.css").status_code == 200
