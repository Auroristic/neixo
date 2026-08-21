# tests/test_dashboard_runner.py
import asyncio
from unittest.mock import MagicMock, patch


def test_start_dashboard_disabled_returns_none(monkeypatch):
    from dashboard import config
    from dashboard.runner import start_dashboard

    monkeypatch.setattr(config, "DASHBOARD_ENABLED", False)
    assert start_dashboard(MagicMock()) is None


def test_start_dashboard_returns_task(monkeypatch):
    from dashboard import config
    from dashboard.runner import start_dashboard

    monkeypatch.setattr(config, "DASHBOARD_ENABLED", True)

    class FakeServer:
        def __init__(self, cfg):
            pass

        async def serve(self):
            await asyncio.sleep(3600)

    with patch("dashboard.runner.uvicorn") as m:
        m.Server = FakeServer
        m.Config = lambda *a, **kw: kw

        async def run():
            t = start_dashboard(MagicMock())
            assert isinstance(t, asyncio.Task)
            t.cancel()

        asyncio.run(run())


def test_start_dashboard_never_raises(monkeypatch):
    from dashboard import config
    from dashboard.runner import start_dashboard

    monkeypatch.setattr(config, "DASHBOARD_ENABLED", True)
    with patch("dashboard.runner.uvicorn", side_effect=RuntimeError("boom")):
        assert start_dashboard(MagicMock()) is None
