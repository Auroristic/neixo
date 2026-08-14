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

def test_trim_history_to_budget_caps_message_count():
    from cogs.ai import _trim_history_to_budget
    tiny = [{"content": "hi"} for _ in range(300)]
    assert len(_trim_history_to_budget(tiny)) == 120


def test_trim_history_to_budget_limits_tokens():
    from cogs.ai import _trim_history_to_budget, HISTORY_TOKEN_BUDGET, _estimate_tokens
    huge = [{"content": "x" * 50_000} for _ in range(120)]
    kept = _trim_history_to_budget(huge)
    total = sum(_estimate_tokens(m.get("content", "")) for m in kept)
    assert total <= HISTORY_TOKEN_BUDGET
    assert len(kept) < 120


def test_trim_history_to_budget_keeps_most_recent():
    from cogs.ai import _trim_history_to_budget
    mixed = [{"content": "y" * 30_000}] * 8 + [{"content": "short"}]
    kept = _trim_history_to_budget(mixed)
    assert kept[-1]["content"] == "short"

def test_pick_reaction_allowed_emoji():
    from cogs.ai import _pick_reaction
    assert _pick_reaction("😭") == "😭"
    assert _pick_reaction("💀") == "💀"
    assert _pick_reaction("  😭  ") == "😭"
    assert _pick_reaction("none") is None
    assert _pick_reaction("") is None
    assert _pick_reaction("hmm maybe 👍") is None  # not a bare emoji reply


def test_embed_builder_flag_parsing():
    from cogs.embedmaker import _build_embed_from_text
    
    embed, err = _build_embed_from_text("My Title | My Description --color blurple --footer \"Custom Footer\" --thumb https://example.com/thumb.png")
    assert err is None
    assert embed.title == "My Title"
    assert embed.description == "My Description"
    assert embed.color.value == 0x5865F2
    assert embed.footer.text == "Custom Footer"
    assert embed.thumbnail.url == "https://example.com/thumb.png"


def test_autoresponse_dynamic_variables():
    from types import SimpleNamespace
    from cogs.autoresponse import _format_response

    mock_msg = SimpleNamespace(
        author=SimpleNamespace(
            mention="<@123>",
            name="testuser",
            display_name="Test User",
            id=123,
            display_avatar=SimpleNamespace(url="https://avatar.url/123.png"),
        ),
        guild=SimpleNamespace(
            name="Super Server",
            id=456,
            member_count=42,
        ),
        channel=SimpleNamespace(
            mention="<#789>",
            name="general",
        ),
    )

    template = "Hey {user.mention}, welcome to {server.name} with {server.count} members in {channel.name}!"
    result = _format_response(template, mock_msg)
    assert result == "Hey <@123>, welcome to Super Server with 42 members in general!"


def test_tts_voices_definition():
    from cogs.voice_tts import FEMALE_VOICES
    assert "ava" in FEMALE_VOICES
    assert "jenny" in FEMALE_VOICES
    assert "sonia" in FEMALE_VOICES
    assert FEMALE_VOICES["ava"]["id"] == "en-US-AvaNeural"

