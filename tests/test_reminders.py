from datetime import datetime, timedelta, timezone

from cogs.reminders import _format_delta, parse_bday, parse_when


class TestParseWhen:
    def test_relative_seconds(self):
        result = parse_when('30s')
        assert result is not None
        assert isinstance(result, datetime)

    def test_relative_minutes(self):
        result = parse_when('5m')
        delta = result - datetime.now(timezone.utc)
        assert 290 < delta.total_seconds() < 310

    def test_relative_hours(self):
        result = parse_when('2h')
        delta = result - datetime.now(timezone.utc)
        assert 7100 < delta.total_seconds() < 7300

    def test_relative_days(self):
        result = parse_when('1d')
        delta = result - datetime.now(timezone.utc)
        assert 86000 < delta.total_seconds() < 87000

    def test_absolute_slash(self):
        result = parse_when('2030/12/25')
        assert result is not None
        assert result.year == 2030
        assert result.month == 12
        assert result.day == 25

    def test_absolute_with_time(self):
        result = parse_when('2030/12/25 14:30')
        assert result is not None
        assert result.hour == 14
        assert result.minute == 30

    def test_invalid_returns_none(self):
        assert parse_when('not_a_time') is None
        assert parse_when('') is None


class TestParseBday:
    def test_mm_dd(self):
        assert parse_bday('11/08') == '11-08'

    def test_mm_dd_dash(self):
        assert parse_bday('12-25') == '12-25'

    def test_full_date(self):
        assert parse_bday('2002/11/08') == '11-08'

    def test_invalid(self):
        assert parse_bday('not_a_date') is None


class TestFormatDelta:
    def test_seconds(self):
        assert _format_delta(timedelta(seconds=30)) == '30s'

    def test_minutes(self):
        assert _format_delta(timedelta(minutes=5, seconds=30)) == '5m 30s'

    def test_hours(self):
        assert _format_delta(timedelta(hours=2, minutes=15)) == '2h 15m'

    def test_days(self):
        assert _format_delta(timedelta(days=3, hours=5)) == '3d 5h'

    def test_exact_hour(self):
        assert _format_delta(timedelta(hours=1)) == '1h'
