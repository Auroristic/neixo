# tests/test_dashboard_app.py
from fastapi.testclient import TestClient
from dashboard.app import create_app


def test_healthz_open():
    client = TestClient(create_app(bot=None))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_root_requires_login():
    client = TestClient(create_app(bot=None), follow_redirects=False)
    r = client.get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_security_headers_present():
    client = TestClient(create_app(bot=None))
    r = client.get("/healthz")
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
