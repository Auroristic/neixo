"""
cogs/digest.py  —  multi-timeframe server digest & personal member cards (daily, weekly, monthly)
"""

import asyncio
import io
import logging
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageDraw, ImageFilter, ImageOps

import aiohttp
import discord
from discord.ext import commands

from utils import DATA_DIR, help_meta, is_owner_or_creator, load_json, save_json
from cogs.serverstats import _load_font

log = logging.getLogger(__name__)

DIGEST_FILE = f"{DATA_DIR}/digest.json"

COG_META = {
    "category": "admin",
    "label": "Admin",
    "desc": "Server analytics digests (daily, weekly, monthly) and personal activity cards.",
}


def _load_digest() -> dict:
    return load_json(DIGEST_FILE) or {}


def _save_digest(state: dict) -> None:
    save_json(DIGEST_FILE, state)


def _timeframe_key(timeframe: str = "weekly") -> str:
    now = datetime.now(timezone.utc)
    tf = (timeframe or "weekly").lower()
    if tf in ("daily", "day", "d"):
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return day.isoformat()
    elif tf in ("monthly", "month", "m"):
        month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return month.isoformat()
    else:  # weekly
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return monday.isoformat()


def _timeframe_label(timeframe: str = "weekly") -> str:
    now = datetime.now(timezone.utc)
    tf = (timeframe or "weekly").lower()
    if tf in ("daily", "day", "d"):
        return f"day of {now.strftime('%b %d, %Y')}"
    elif tf in ("monthly", "month", "m"):
        return f"month of {now.strftime('%B %Y')}"
    else:
        return f"week of {now.strftime('%b %d')}"


def _normalize_timeframe(tf_raw: str | None) -> str:
    if not tf_raw:
        return "weekly"
    low = tf_raw.lower().strip()
    if low in ("daily", "day", "d", "today", "24h"):
        return "daily"
    if low in ("monthly", "month", "m", "30d"):
        return "monthly"
    return "weekly"


def _truncate_text(font, text: str, max_width: int) -> str:
    if font.getlength(text) <= max_width:
        return text
    ell = "..."
    ell_w = font.getlength(ell)
    if ell_w >= max_width:
        return text[:1] if text else ""
    for i in range(len(text) - 1, 0, -1):
        sub = text[:i]
        if font.getlength(sub) + ell_w <= max_width:
            return sub + ell
    return (text[:1] if text else "") + ell


def _make_glass_backdrop(
    source_bytes: bytes | None,
    width: int,
    height: int,
    dark_tint: float = 0.55,
    blur_radius: int = 24,
) -> Image.Image:
    """Generate an authentic, recognizable deep dark Black & White frosted glass backdrop."""
    from PIL import ImageEnhance
    if source_bytes:
        try:
            src = Image.open(io.BytesIO(source_bytes)).convert("RGB")
            src_bw = ImageOps.grayscale(src).convert("RGB")
            src_bw = ImageEnhance.Brightness(src_bw).enhance(0.70)
            src_bw = ImageEnhance.Contrast(src_bw).enhance(1.15)
            bg = ImageOps.fit(src_bw, (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            bg = bg.filter(ImageFilter.GaussianBlur(blur_radius))
        except Exception:
            bg = Image.new("RGB", (width, height), (8, 9, 12))
    else:
        bg = Image.new("RGB", (width, height), (8, 9, 12))

    overlay = Image.new("RGB", (width, height), (5, 6, 8))
    bg = Image.blend(bg, overlay, dark_tint)

    grad = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(height):
        alpha = int(65 * (y / height))
        gd.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
    bg = Image.alpha_composite(bg.convert("RGBA"), grad)
    return bg


def _fmt_vc(secs: int) -> str:
    hours, rem = divmod(secs, 3600)
    mins = rem // 60
    if hours > 0:
        return f"{hours}h {mins:02d}m"
    return f"{mins}m"


def _render_digest_card(
    icon_bytes: bytes | None,
    guild_name: str,
    week_label: str,
    msg_total: int,
    vc_str: str,
    bumps_total: int,
    member_growth: int,
    chatters: list[tuple[int, str, int]],
    vc_top: list[tuple[int, str, int]],
    bumper_top: list[tuple[int, str, int]],
    timeframe: str = "weekly",
) -> io.BytesIO:
    base_header_h = 280
    sec1_h = (45 + (len(chatters) * 40) + 24) if chatters else 0
    sec2_h = (45 + (len(vc_top) * 40) + 24) if vc_top else 0
    sec3_h = (45 + (len(bumper_top) * 40) + 24) if bumper_top else 0
    footer_h = 90

    W = 900
    H = max(1100, base_header_h + sec1_h + sec2_h + sec3_h + footer_h)

    bg = _make_glass_backdrop(icon_bytes, W, H, dark_tint=0.55, blur_radius=28)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad = 45
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=32, fill=(0, 0, 0, 95))
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=32, outline=(255, 255, 255, 55), width=1)
    cd.line([(pad + 30, pad + 1), (W - pad - 30, pad + 1)], fill=(255, 255, 255, 95), width=1)
    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    title_font = _load_font(42, bold=True)
    sub_font = _load_font(22, bold=False)
    label_font = _load_font(24, bold=True)
    row_font = _load_font(22, bold=False)
    small_font = _load_font(18, bold=False)

    tf_title = "daily digest" if timeframe == "daily" else ("monthly rewind" if timeframe == "monthly" else "weekly digest")
    title_font.draw(draw, (85, 70), tf_title, fill=(255, 255, 255, 255))
    sub_font.draw(draw, (85, 130), f"{guild_name} · {week_label}", fill=(180, 185, 195, 200))
    draw.line([(85, 185), (W - 85, 185)], fill=(255, 255, 255, 35), width=1)

    y = 220
    label_font.draw(draw, (85, y), f"{msg_total:,} messages", fill=(245, 248, 255, 240))
    growth_str = f"+{member_growth:,} members" if member_growth >= 0 else f"{member_growth:,} members"
    cw = label_font.getlength(growth_str)
    label_font.draw(draw, (W - 85 - cw, y), growth_str, fill=(245, 248, 255, 240))
    y += 40
    label_font.draw(draw, (85, y), f"{vc_str} in voice", fill=(245, 248, 255, 240))
    cw = label_font.getlength(f"{bumps_total} bumps")
    label_font.draw(draw, (W - 85 - cw, y), f"{bumps_total} bumps", fill=(245, 248, 255, 240))
    y += 70

    def _section(label, rows, unit):
        nonlocal y
        if not rows:
            return
        label_font.draw(draw, (85, y), label, fill=(160, 165, 175, 180))
        y += 45
        for rank, name, val in rows:
            row_font.draw(draw, (85, y), f"{rank}.", fill=(135, 140, 150, 230))
            v = f"{val:,}{unit}"
            vw = row_font.getlength(v)
            max_name_w = W - 85 - vw - 160
            clean_name = _truncate_text(row_font, str(name or "Unknown"), max_name_w)
            row_font.draw(draw, (135, y), clean_name, fill=(235, 240, 248, 230))
            row_font.draw(draw, (W - 85 - vw, y), v, fill=(200, 205, 215, 220))
            y += 40
        y += 24

    _section("top chatters", chatters, " msgs")
    _section("most in voice", vc_top, "m")
    _section("top bumpers", bumper_top, " bumps")

    footer_y = y + 10
    draw.line([(85, footer_y), (W - 85, footer_y)], fill=(255, 255, 255, 35), width=1)
    small_font.draw(draw, (85, footer_y + 25), "xo", fill=(160, 165, 175, 180))

    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG", quality=92)
    buf.seek(0)
    return buf


def _render_member_digest_card(
    avatar_bytes: bytes | None,
    guild_icon_bytes: bytes | None,
    member_name: str,
    member_handle: str,
    joined_str: str,
    weekly_msgs: int,
    total_msgs: int,
    msg_rank: int | None,
    weekly_vc_secs: int,
    total_vc_secs: int,
    vc_rank: int | None,
    weekly_bumps: int,
    total_bumps: int,
    bump_rank: int | None,
    share_pct: float = 0.0,
) -> io.BytesIO:
    W = 860
    H = 480

    bg_source = avatar_bytes or guild_icon_bytes
    bg = _make_glass_backdrop(bg_source, W, H, dark_tint=0.55, blur_radius=32)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad = 32
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=28, fill=(4, 5, 8, 140))
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=28, outline=(255, 255, 255, 45), width=1)
    cd.line([(pad + 25, pad + 1), (W - pad - 25, pad + 1)], fill=(255, 255, 255, 90), width=1)

    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    title_font = _load_font(28, bold=True)
    sub_font = _load_font(18, bold=False)
    name_font = _load_font(24, bold=True)
    val_font = _load_font(22, bold=True)
    label_font = _load_font(15, bold=False)
    badge_font = _load_font(14, bold=True)
    small_font = _load_font(14, bold=False)

    # Left Column: Avatar & Profile Info
    av_size = 110
    av_x = 65
    av_y = 65
    if avatar_bytes:
        try:
            av_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            av_img = ImageOps.fit(av_img, (av_size, av_size), method=Image.Resampling.LANCZOS)
            mask = Image.new("L", (av_size, av_size), 0)
            md = ImageDraw.Draw(mask)
            md.ellipse([0, 0, av_size, av_size], fill=255)
            av_circle = Image.new("RGBA", (av_size, av_size), (0, 0, 0, 0))
            av_circle.paste(av_img, (0, 0), mask=mask)
            bg.paste(av_circle, (av_x, av_y), mask=av_circle)
            draw.ellipse([av_x, av_y, av_x + av_size, av_y + av_size], outline=(255, 255, 255, 120), width=2)
        except Exception:
            draw.ellipse([av_x, av_y, av_x + av_size, av_y + av_size], fill=(25, 26, 32), outline=(255, 255, 255, 80), width=2)
    else:
        draw.ellipse([av_x, av_y, av_x + av_size, av_y + av_size], fill=(25, 26, 32), outline=(255, 255, 255, 80), width=2)

    clean_name = _truncate_text(name_font, member_name, 220)
    name_font.draw(draw, (av_x, av_y + av_size + 16), clean_name, fill=(255, 255, 255, 245))
    clean_handle = _truncate_text(sub_font, f"@{member_handle}", 220)
    sub_font.draw(draw, (av_x, av_y + av_size + 48), clean_handle, fill=(160, 165, 175, 200))
    if joined_str:
        small_font.draw(draw, (av_x, av_y + av_size + 78), f"joined {joined_str}", fill=(120, 125, 135, 180))

    # Divider between left and right
    draw.line([(310, 65), (310, H - 65)], fill=(255, 255, 255, 30), width=1)

    # Right Panel: Activity & Ranks
    rx = 345
    title_font.draw(draw, (rx, 65), "personal digest", fill=(255, 255, 255, 255))
    sub_font.draw(draw, (rx, 102), "this week's server contributions", fill=(175, 180, 190, 200))

    card_w = W - pad - rx - 20
    card_h = 76
    cards_data = [
        ("messages", f"{total_msgs:,} messages", f"+{weekly_msgs:,} this week", f"#{msg_rank}" if msg_rank else "-"),
        ("voice", f"{_fmt_vc(total_vc_secs)} voice", f"+{_fmt_vc(weekly_vc_secs)} this week", f"#{vc_rank}" if vc_rank else "-"),
        ("bumps", f"{total_bumps:,} bumps", f"+{weekly_bumps:,} this week", f"#{bump_rank}" if bump_rank else "-"),
    ]

    cy = 145
    for label, main_str, sub_str, rank_str in cards_data:
        draw.rounded_rectangle([rx, cy, rx + card_w, cy + card_h], radius=14, fill=(12, 14, 18, 160), outline=(255, 255, 255, 35), width=1)

        draw.rounded_rectangle([rx + 14, cy + 18, rx + 65, cy + card_h - 18], radius=8, fill=(255, 255, 255, 18), outline=(255, 255, 255, 40), width=1)
        rw = badge_font.getlength(rank_str)
        badge_font.draw(draw, (rx + 14 + (51 - rw) / 2, cy + 28), rank_str, fill=(245, 248, 255, 240))

        val_font.draw(draw, (rx + 82, cy + 15), main_str, fill=(250, 252, 255, 245))
        label_font.draw(draw, (rx + 82, cy + 44), f"{label} · {sub_str}", fill=(160, 165, 175, 190))

        cy += card_h + 12

    if share_pct > 0:
        small_font.draw(draw, (rx, H - 56), f"accounted for {share_pct:.1f}% of active messages this week", fill=(140, 145, 155, 180))
    else:
        small_font.draw(draw, (rx, H - 56), "xo personal activity card", fill=(140, 145, 155, 180))

    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG", quality=92)
    buf.seek(0)
    return buf


def _get_timeframe_conf(guild_conf: dict, timeframe: str) -> dict:
    """Returns the sub-config dict for a specific timeframe with backwards compatibility."""
    if "schedules" not in guild_conf:
        guild_conf["schedules"] = {}

    # Migrate legacy root keys to weekly schedule if present
    if "weekly" not in guild_conf["schedules"] and "channel_id" in guild_conf:
        guild_conf["schedules"]["weekly"] = {
            "channel_id": guild_conf.get("channel_id"),
            "enabled": True,
            "baselines": guild_conf.get("baselines", {}),
            "member_base": guild_conf.get("member_base", 0),
            "last_run_iso": guild_conf.get("last_run_iso", ""),
        }

    if timeframe not in guild_conf["schedules"]:
        guild_conf["schedules"][timeframe] = {
            "channel_id": guild_conf.get("channel_id", ""),
            "enabled": False,
            "baselines": {},
            "member_base": 0,
            "last_run_iso": "",
        }

    return guild_conf["schedules"][timeframe]


class Digest(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.task: asyncio.Task | None = None

    async def cog_load(self):
        self.task = asyncio.create_task(self._loop())

    def cog_unload(self):
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = None

    async def _loop(self):
        await self.bot.wait_until_ready()
        try:
            while not self.bot.is_closed():
                try:
                    await self._maybe_run()
                except Exception as e:
                    log.warning("digest check error: %s", e)
                await asyncio.sleep(1800)  # Check every 30 mins
        except asyncio.CancelledError:
            pass

    def _get_timeframe_conf(self, guild_conf: dict, timeframe: str) -> dict:
        return _get_timeframe_conf(guild_conf, timeframe)

    async def _maybe_run(self):
        state = _load_digest()
        if not state:
            return
        now = datetime.now(timezone.utc)

        for gid_str, conf in state.items():
            guild = self.bot.get_guild(int(gid_str))
            if guild is None:
                continue

            # Check all 3 timeframes
            timeframes = ["daily", "weekly", "monthly"]
            for tf in timeframes:
                tf_conf = self._get_timeframe_conf(conf, tf)
                if not tf_conf.get("enabled", False) or not tf_conf.get("channel_id"):
                    continue

                should_run = False
                if tf == "daily":
                    # Run if new day started (UTC)
                    if tf_conf.get("last_run_iso", "") < _timeframe_key("daily"):
                        should_run = True
                elif tf == "weekly":
                    # Sunday UTC
                    if now.weekday() == 6 and tf_conf.get("last_run_iso", "") < _timeframe_key("weekly"):
                        should_run = True
                elif tf == "monthly":
                    # 1st of month UTC
                    if now.day == 1 and tf_conf.get("last_run_iso", "") < _timeframe_key("monthly"):
                        should_run = True

                if should_run:
                    try:
                        await self._run_digest(guild, tf_conf, timeframe=tf)
                        tf_conf["last_run_iso"] = now.isoformat()
                        _save_digest(state)
                    except Exception as e:
                        log.warning("digest failed for %s (%s): %s", gid_str, tf, e)

    def _baselines(self, gid_str: str, conf: dict, update: bool = True) -> tuple[dict, dict, dict]:
        base = conf.setdefault("baselines", {})
        from cogs.serverstats import _get_conn as _stats_conn
        msgs = dict(_stats_conn().execute(
            "SELECT user_id, count FROM message_counts WHERE guild_id = ?", (int(gid_str),)
        ).fetchall())
        vc = dict(_stats_conn().execute(
            "SELECT user_id, total_seconds FROM vc_time WHERE guild_id = ?", (int(gid_str),)
        ).fetchall())
        try:
            from cogs.bumps import _get_conn as _bumps_conn
            bumps = dict(_bumps_conn().execute(
                "SELECT user_id, count FROM bump_counts WHERE guild_id = ?", (gid_str,)
            ).fetchall())
        except Exception:
            bumps = {}

        delta_msgs: dict[int, int] = {}
        delta_vc: dict[int, int] = {}
        delta_bumps: dict[int, int] = {}
        for uid, cur in msgs.items():
            prev = base.get(str(uid), {}).get("msgs", 0)
            if cur - prev > 0:
                delta_msgs[uid] = cur - prev
        for uid, cur in vc.items():
            prev = base.get(str(uid), {}).get("vc", 0)
            if cur - prev > 0:
                delta_vc[uid] = cur - prev
        for uid, cur in bumps.items():
            prev = base.get(str(uid), {}).get("bumps", 0)
            if cur - prev > 0:
                delta_bumps[int(uid)] = cur - prev

        if update:
            for uid, cur in msgs.items():
                base.setdefault(str(uid), {})["msgs"] = cur
            for uid, cur in vc.items():
                base.setdefault(str(uid), {})["vc"] = cur
            for uid, cur in bumps.items():
                base.setdefault(str(uid), {})["bumps"] = cur
        return (delta_msgs, delta_vc, delta_bumps)

    async def _build_digest_file(self, guild: discord.Guild, conf: dict, update: bool, timeframe: str = "weekly") -> io.BytesIO | None:
        gid_str = str(guild.id)
        delta_msgs, delta_vc, delta_bumps = self._baselines(gid_str, conf, update=update)

        def _name(uid):
            m = guild.get_member(uid)
            return m.display_name if m else f"<@{uid}>"

        chatters = sorted(delta_msgs.items(), key=lambda x: -x[1])[:5]
        vc_rows = sorted(delta_vc.items(), key=lambda x: -x[1])[:5]
        bumper_rows = sorted(delta_bumps.items(), key=lambda x: -x[1])[:5]

        member_growth = (guild.member_count or len(guild.members)) - conf.get("member_base", guild.member_count or len(guild.members))

        icon_bytes = None
        try:
            if guild.icon:
                async with aiohttp.ClientSession() as s:
                    async with s.get(guild.icon.url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                        if r.status == 200:
                            icon_bytes = await r.read()
        except Exception:
            pass

        label = _timeframe_label(timeframe)
        return await asyncio.to_thread(
            _render_digest_card,
            icon_bytes,
            guild.name,
            label,
            sum(delta_msgs.values()),
            _fmt_vc(sum(delta_vc.values())),
            sum(delta_bumps.values()),
            member_growth,
            [(i + 1, _name(uid), n) for i, (uid, n) in enumerate(chatters)],
            [(i + 1, _name(uid), n // 60) for i, (uid, n) in enumerate(vc_rows)],
            [(i + 1, _name(uid), n) for i, (uid, n) in enumerate(bumper_rows)],
            timeframe,
        )

    async def _build_member_digest_file(self, guild: discord.Guild, member: discord.Member) -> io.BytesIO | None:
        gid_str = str(guild.id)
        state = _load_digest()
        conf = state.get(gid_str, {})
        weekly_conf = self._get_timeframe_conf(conf, "weekly")

        # Weekly deltas
        delta_msgs, delta_vc, delta_bumps = self._baselines(gid_str, weekly_conf, update=False)

        # All-time totals from DB
        from cogs.serverstats import _get_conn as _stats_conn
        all_msgs = dict(_stats_conn().execute(
            "SELECT user_id, count FROM message_counts WHERE guild_id = ?", (guild.id,)
        ).fetchall())
        all_vc = dict(_stats_conn().execute(
            "SELECT user_id, total_seconds FROM vc_time WHERE guild_id = ?", (guild.id,)
        ).fetchall())
        try:
            from cogs.bumps import _get_conn as _bumps_conn
            all_bumps = dict(_bumps_conn().execute(
                "SELECT user_id, count FROM bump_counts WHERE guild_id = ?", (gid_str,)
            ).fetchall())
        except Exception:
            all_bumps = {}

        # Rankings
        sorted_msgs = sorted(all_msgs.items(), key=lambda x: -x[1])
        msg_rank = next((i + 1 for i, (uid, _) in enumerate(sorted_msgs) if uid == member.id), None)

        sorted_vc = sorted(all_vc.items(), key=lambda x: -x[1])
        vc_rank = next((i + 1 for i, (uid, _) in enumerate(sorted_vc) if uid == member.id), None)

        sorted_bumps = sorted(all_bumps.items(), key=lambda x: -x[1])
        bump_rank = next((i + 1 for i, (uid, _) in enumerate(sorted_bumps) if str(uid) == str(member.id)), None)

        user_weekly_msgs = delta_msgs.get(member.id, 0)
        user_total_msgs = all_msgs.get(member.id, 0)

        user_weekly_vc = delta_vc.get(member.id, 0)
        user_total_vc = all_vc.get(member.id, 0)

        user_weekly_bumps = delta_bumps.get(member.id, 0)
        user_total_bumps = all_bumps.get(str(member.id), all_bumps.get(member.id, 0))

        tot_weekly_msgs = sum(delta_msgs.values()) or 1
        share_pct = (user_weekly_msgs / tot_weekly_msgs) * 100 if user_weekly_msgs > 0 else 0.0

        avatar_bytes = None
        guild_icon_bytes = None
        async with aiohttp.ClientSession() as s:
            try:
                av_url = member.display_avatar.url if member.display_avatar else None
                if av_url:
                    async with s.get(av_url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                        if r.status == 200:
                            avatar_bytes = await r.read()
            except Exception:
                pass
            try:
                if guild.icon:
                    async with s.get(guild.icon.url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                        if r.status == 200:
                            guild_icon_bytes = await r.read()
            except Exception:
                pass

        joined_str = member.joined_at.strftime("%b %Y") if member.joined_at else ""

        return await asyncio.to_thread(
            _render_member_digest_card,
            avatar_bytes,
            guild_icon_bytes,
            member.display_name,
            member.name,
            joined_str,
            user_weekly_msgs,
            user_total_msgs,
            msg_rank,
            user_weekly_vc,
            user_total_vc,
            vc_rank,
            user_weekly_bumps,
            user_total_bumps,
            bump_rank,
            share_pct,
        )

    async def _run_digest(self, guild: discord.Guild, tf_conf: dict, timeframe: str = "weekly"):
        buf = await self._build_digest_file(guild, tf_conf, update=True, timeframe=timeframe)
        if buf is None:
            return
        channel = guild.get_channel(int(tf_conf["channel_id"]))
        if channel is not None:
            try:
                await channel.send(file=discord.File(fp=buf, filename=f"digest_{timeframe}.png"))
            except discord.HTTPException:
                pass

    @commands.group(name="digest", invoke_without_command=True)
    @help_meta(
        usage="`.digest <#channel>`  ·  `.digest now [daily|weekly|monthly]`  ·  `.digest me`  ·  `.digest status`",
        desc="Server analytics digest cards (daily, weekly, monthly) and personal activity summaries.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".digest #general", ".digest now weekly", ".digest now daily", ".digest me", ".digest status", ".digest off"],
        params=[],
        note="Requires Administrator or Manage Server permission. Automatically broadcasts on schedule.",
    )
    async def digest(self, ctx: commands.Context, channel: discord.TextChannel = None):
        if channel is not None:
            return await self.digest_set(ctx, channel=channel, timeframe="weekly")
        await ctx.send(
            "-# **digest commands**:\n"
            "-# • `.digest me` / `.mydigest` — view your personal activity card\n"
            "-# • `.digest now [daily|weekly|monthly]` — preview server digest card\n"
            "-# • `.digest set <#channel> [daily|weekly|monthly]` — configure auto digest\n"
            "-# • `.digest status` — view active schedules\n"
            "-# • `.digest off [daily|weekly|monthly|all]` — disable scheduled digest"
        )

    async def _admin(self, ctx) -> bool:
        if ctx.guild is None:
            return False
        if is_owner_or_creator(ctx):
            return True
        perms = getattr(ctx.author, "guild_permissions", None)
        return bool(perms and perms.administrator)

    @digest.command(name="me", aliases=["user", "u", "my"])
    @help_meta(
        usage="`.digest me`  ·  `.digest @user`",
        desc="Generates a personalized member activity and server ranking digest card.",
        section="Utility",
        perm_tier="public",
        examples=[".digest me", ".digest @user", ".mydigest"],
        params=[{"name": "member", "type": "user", "required": False, "desc": "Target member (defaults to yourself)."}],
        note="Shows messages, voice hours, and server bump rankings.",
    )
    async def digest_me(self, ctx: commands.Context, member: discord.Member = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        target = member or ctx.author
        async with ctx.typing():
            buf = await self._build_member_digest_file(ctx.guild, target)
        if buf is None:
            return await ctx.send("-# failed to generate personal digest card.")
        await ctx.send(file=discord.File(fp=buf, filename=f"digest_{target.id}.png"))

    @commands.command(name="mydigest", aliases=["myd", "myactivity"])
    @help_meta(
        usage="`.mydigest`",
        desc="Fast shortcut to generate your personal activity and server ranking card.",
        section="Utility",
        perm_tier="public",
        examples=[".mydigest"],
        params=[],
        note="Shortcut for `.digest me`.",
    )
    async def mydigest_fast(self, ctx: commands.Context):
        await self.digest_me(ctx, member=ctx.author)

    @digest.command(name="daily", aliases=["day"])
    @help_meta(
        usage="`.digest daily`",
        desc="Generates and previews today's 24-hour server digest card.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".digest daily"],
        params=[],
        note="Requires Administrator permission.",
    )
    async def digest_daily_cmd(self, ctx: commands.Context):
        await self.digest_now(ctx, timeframe="daily")

    @digest.command(name="weekly", aliases=["week"])
    @help_meta(
        usage="`.digest weekly`",
        desc="Generates and previews this week's 7-day server digest card.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".digest weekly"],
        params=[],
        note="Requires Administrator permission.",
    )
    async def digest_weekly_cmd(self, ctx: commands.Context):
        await self.digest_now(ctx, timeframe="weekly")

    @digest.command(name="monthly", aliases=["month"])
    @help_meta(
        usage="`.digest monthly`",
        desc="Generates and previews this month's 30-day server digest card.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".digest monthly"],
        params=[],
        note="Requires Administrator permission.",
    )
    async def digest_monthly_cmd(self, ctx: commands.Context):
        await self.digest_now(ctx, timeframe="monthly")

    @digest.command(name="off")
    @help_meta(
        usage="`.digest off [daily|weekly|monthly|all]`",
        desc="Disables scheduled digest cards for a timeframe or all schedules.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".digest off", ".digest off daily", ".digest off all"],
        params=[{"name": "timeframe", "type": "str", "required": False, "desc": "daily, weekly, monthly, or all (default)." }],
        note="Requires Administrator permission.",
    )
    async def digest_off(self, ctx: commands.Context, timeframe: str = "all"):
        if not await self._admin(ctx):
            return await ctx.send("-# admin only")
        state = _load_digest()
        gid = str(ctx.guild.id)
        if gid not in state:
            return await ctx.send("-# digest is not configured for this server.")

        tf = timeframe.lower().strip() if timeframe else "all"
        if tf in ("all", "*"):
            state.pop(gid, None)
            _save_digest(state)
            await ctx.send("-# disabled all scheduled digest broadcasts.")
        else:
            norm_tf = _normalize_timeframe(tf)
            conf = state[gid]
            if "schedules" in conf and norm_tf in conf["schedules"]:
                conf["schedules"][norm_tf]["enabled"] = False
                _save_digest(state)
                await ctx.send(f"-# disabled **{norm_tf}** digest broadcasts.")
            else:
                await ctx.send(f"-# **{norm_tf}** digest was not active.")

    @digest.command(name="status")
    @help_meta(
        usage="`.digest status`",
        desc="Shows active digest schedules (daily, weekly, monthly) and their destination channels.",
        section="Server Management",
        perm_tier="public",
        examples=[".digest status"],
        params=[],
        note="Available to all members.",
    )
    async def digest_status(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        state = _load_digest()
        conf = state.get(str(ctx.guild.id))
        if not conf:
            return await ctx.send("-# digest is off. `.digest set <#channel> [daily|weekly|monthly]` to enable.")

        lines = ["-# **server digest schedules**:"]
        active_any = False
        for tf in ["daily", "weekly", "monthly"]:
            tf_conf = self._get_timeframe_conf(conf, tf)
            if tf_conf.get("enabled") and tf_conf.get("channel_id"):
                ch = ctx.guild.get_channel(int(tf_conf["channel_id"]))
                ch_mention = ch.mention if ch else f"`#{tf_conf['channel_id']}`"
                sched_desc = "every day at 00:00 UTC" if tf == "daily" else ("every Sunday at 00:00 UTC" if tf == "weekly" else "1st of every month at 00:00 UTC")
                lines.append(f"-# • **{tf.title()}**: {ch_mention} ({sched_desc})")
                active_any = True

        if not active_any:
            return await ctx.send("-# all digest schedules are currently off. use `.digest set <#channel> [timeframe]` to enable.")

        await ctx.send("\n".join(lines))

    @digest.command(name="now")
    @help_meta(
        usage="`.digest now [daily|weekly|monthly]`",
        desc="Generates and sends an on-demand preview of the server digest card.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".digest now", ".digest now daily", ".digest now monthly"],
        params=[{"name": "timeframe", "type": "str", "required": False, "desc": "daily, weekly (default), or monthly."}],
        note="Requires Administrator permission. Does not reset baseline tracking.",
    )
    async def digest_now(self, ctx: commands.Context, timeframe: str = "weekly"):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if not await self._admin(ctx):
            return await ctx.send("-# admin only")

        norm_tf = _normalize_timeframe(timeframe)
        state = _load_digest()
        conf = state.get(str(ctx.guild.id), {})
        tf_conf = self._get_timeframe_conf(conf, norm_tf)

        async with ctx.typing():
            buf = await self._build_digest_file(ctx.guild, tf_conf, update=False, timeframe=norm_tf)
        if buf is None:
            return await ctx.send(f"-# failed to generate {norm_tf} digest card.")
        await ctx.send(file=discord.File(fp=buf, filename=f"digest_{norm_tf}.png"))

    @digest.command(name="set", aliases=["on", "schedule"])
    @help_meta(
        usage="`.digest set <#channel> [daily|weekly|monthly]`",
        desc="Enables automated digest broadcasts for a channel and timeframe.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".digest set #general", ".digest set #announcements daily", ".digest set #recap monthly"],
        params=[
            {"name": "channel", "type": "channel", "required": True, "desc": "Destination announcement channel."},
            {"name": "timeframe", "type": "str", "required": False, "desc": "daily, weekly (default), or monthly."},
        ],
        note="Requires Administrator or Manage Server permission.",
    )
    async def digest_set(self, ctx: commands.Context, channel: discord.TextChannel = None, timeframe: str = "weekly"):
        if not await self._admin(ctx):
            return await ctx.send("-# admin only")
        if channel is None:
            return await ctx.send("-# usage: `.digest set <#channel> [daily|weekly|monthly]`")

        norm_tf = _normalize_timeframe(timeframe)
        state = _load_digest()
        gid = str(ctx.guild.id)
        if gid not in state:
            state[gid] = {"channel_id": str(channel.id), "schedules": {}}

        conf = state[gid]
        tf_conf = self._get_timeframe_conf(conf, norm_tf)
        tf_conf["channel_id"] = str(channel.id)
        tf_conf["enabled"] = True
        tf_conf["member_base"] = ctx.guild.member_count or len(ctx.guild.members)
        tf_conf["last_run_iso"] = ""

        self._baselines(gid, tf_conf, update=True)
        _save_digest(state)

        sched_desc = "daily at 00:00 UTC" if norm_tf == "daily" else ("every Sunday" if norm_tf == "weekly" else "monthly on the 1st")
        await ctx.send(f"-# **{norm_tf.title()}** digest active → {channel.mention} ({sched_desc}).")


async def setup(bot: commands.Bot):
    await bot.add_cog(Digest(bot))
