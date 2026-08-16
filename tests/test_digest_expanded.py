"""
tests/test_digest_expanded.py  —  unit tests for multi-timeframe digests and personal member cards
"""

import io
from datetime import datetime, timezone
from PIL import Image

import pytest
from cogs.digest import (
    _fmt_vc,
    _get_timeframe_conf,
    _normalize_timeframe,
    _render_digest_card,
    _render_member_digest_card,
    _timeframe_key,
    _timeframe_label,
)


def test_timeframe_helpers():
    # Normalize timeframe
    assert _normalize_timeframe("daily") == "daily"
    assert _normalize_timeframe("day") == "daily"
    assert _normalize_timeframe("today") == "daily"
    assert _normalize_timeframe("weekly") == "weekly"
    assert _normalize_timeframe("week") == "weekly"
    assert _normalize_timeframe("monthly") == "monthly"
    assert _normalize_timeframe("month") == "monthly"
    assert _normalize_timeframe(None) == "weekly"
    assert _normalize_timeframe("unknown") == "weekly"

    # Timeframe keys (ISO format start dates)
    daily_key = _timeframe_key("daily")
    weekly_key = _timeframe_key("weekly")
    monthly_key = _timeframe_key("monthly")

    assert len(daily_key) >= 19
    assert len(weekly_key) >= 19
    assert len(monthly_key) >= 19

    # Timeframe labels
    assert "day of" in _timeframe_label("daily")
    assert "week of" in _timeframe_label("weekly")
    assert "month of" in _timeframe_label("monthly")


def test_fmt_vc():
    assert _fmt_vc(0) == "0m"
    assert _fmt_vc(120) == "2m"
    assert _fmt_vc(3600) == "1h 00m"
    assert _fmt_vc(7380) == "2h 03m"


def test_get_timeframe_conf_backwards_compatibility():
    # Legacy config with top-level channel_id & baselines
    legacy_conf = {
        "channel_id": "123456789",
        "member_base": 50,
        "last_run_iso": "2026-08-01T00:00:00+00:00",
        "baselines": {"111": {"msgs": 100, "vc": 50, "bumps": 5}},
    }

    # Weekly should inherit legacy data
    weekly = _get_timeframe_conf(legacy_conf, "weekly")
    assert weekly["channel_id"] == "123456789"
    assert weekly["enabled"] is True
    assert weekly["baselines"]["111"]["msgs"] == 100

    # Daily should be initialized empty and disabled by default
    daily = _get_timeframe_conf(legacy_conf, "daily")
    assert daily["enabled"] is False
    assert daily["channel_id"] == "123456789"

    # Monthly should also be initialized
    monthly = _get_timeframe_conf(legacy_conf, "monthly")
    assert monthly["enabled"] is False


def test_render_digest_card_timeframes():
    chatters = [(1, "User Alpha", 150), (2, "User Beta", 95)]
    vc_top = [(1, "User Gamma", 120), (2, "User Delta", 45)]
    bumpers = [(1, "User Epsilon", 14)]

    # 1. Weekly digest card
    buf_w = _render_digest_card(
        icon_bytes=None,
        guild_name="Seoulities",
        week_label="week of Aug 16",
        msg_total=4200,
        vc_str="14h 30m",
        bumps_total=32,
        member_growth=12,
        chatters=chatters,
        vc_top=vc_top,
        bumper_top=bumpers,
        timeframe="weekly",
    )
    assert isinstance(buf_w, io.BytesIO)
    img_w = Image.open(buf_w)
    assert img_w.size[0] == 900
    assert img_w.size[1] >= 1100

    # 2. Daily digest card
    buf_d = _render_digest_card(
        icon_bytes=None,
        guild_name="Seoulities",
        week_label="day of Aug 16, 2026",
        msg_total=640,
        vc_str="2h 15m",
        bumps_total=5,
        member_growth=2,
        chatters=chatters,
        vc_top=vc_top,
        bumper_top=bumpers,
        timeframe="daily",
    )
    assert isinstance(buf_d, io.BytesIO)
    img_d = Image.open(buf_d)
    assert img_d.size[0] == 900

    # 3. Monthly digest card
    buf_m = _render_digest_card(
        icon_bytes=None,
        guild_name="Seoulities",
        week_label="month of August 2026",
        msg_total=18500,
        vc_str="62h 40m",
        bumps_total=128,
        member_growth=48,
        chatters=chatters,
        vc_top=vc_top,
        bumper_top=bumpers,
        timeframe="monthly",
    )
    assert isinstance(buf_m, io.BytesIO)
    img_m = Image.open(buf_m)
    assert img_m.size[0] == 900


def test_render_member_digest_card():
    # Test generating a fake avatar in memory
    av = Image.new("RGBA", (128, 128), (255, 180, 200, 255))
    av_bytes = io.BytesIO()
    av.save(av_bytes, format="PNG")
    av_raw = av_bytes.getvalue()

    buf = _render_member_digest_card(
        avatar_bytes=av_raw,
        guild_icon_bytes=None,
        member_name="retroistic",
        member_handle="retro",
        joined_str="Dec 2024",
        weekly_msgs=450,
        total_msgs=14200,
        msg_rank=1,
        weekly_vc_secs=18200,
        total_vc_secs=250000,
        vc_rank=3,
        weekly_bumps=14,
        total_bumps=120,
        bump_rank=2,
        share_pct=14.5,
    )

    assert isinstance(buf, io.BytesIO)
    img = Image.open(buf)
    assert img.size[0] == 860
    assert img.size[1] == 480
