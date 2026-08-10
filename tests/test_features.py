from cogs.giveaways import _parse_duration
from cogs.reminders import parse_interval
from cogs.afk import _ago
from datetime import datetime, timedelta, timezone


def test_parse_duration():
    assert _parse_duration("30m") == 1800
    assert _parse_duration("2h") == 7200
    assert _parse_duration("1d") == 86400
    assert _parse_duration("1w") == 604800
    assert _parse_duration("5s") == 5
    assert _parse_duration("10") is None
    assert _parse_duration("xm") is None
    assert _parse_duration("") is None


def test_parse_interval():
    assert parse_interval("5m") == 300
    assert parse_interval("2h") == 7200
    assert parse_interval("1d") == 86400
    assert parse_interval("1w") == 604800
    assert parse_interval("10s") == 10
    assert parse_interval("0m") is None
    assert parse_interval("abc") is None
    assert parse_interval("2026/11/08") is None


def test_ago():
    now = datetime.now(timezone.utc)
    assert _ago((now - timedelta(seconds=30)).isoformat()) == "30s"
    assert _ago((now - timedelta(minutes=5)).isoformat()) == "5m"
    assert _ago((now - timedelta(hours=3)).isoformat()) == "3h"
    assert _ago((now - timedelta(days=2)).isoformat()) == "2d"
    assert _ago("garbage") == "?"
