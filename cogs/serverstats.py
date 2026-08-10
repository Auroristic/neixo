"""
cogs/serverstats.py  —  Server statistics, leaderboards, and .seoulities info card
"""

import asyncio
import io
import logging
import os
import re
import sqlite3 as _sql
import time

import aiohttp
import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from utils import DATA_DIR, help_meta

log = logging.getLogger(__name__)

SERVERSTATS_DB = f"{DATA_DIR}/serverstats.db"
os.makedirs(DATA_DIR, exist_ok=True)

# ── Font paths (JetBrains Mono preferred, fallback chain) ───────────
_FONT_REG_PATHS = [
    "/usr/share/fonts/truetype/jetbrains/JetBrainsMono-Regular.ttf",
    "/usr/share/fonts/opentype/jetbrains/JetBrainsMono-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "arial.ttf",
]
_FONT_BOLD_PATHS = [
    "/usr/share/fonts/truetype/jetbrains/JetBrainsMono-Bold.ttf",
    "/usr/share/fonts/opentype/jetbrains/JetBrainsMono-Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "arialbd.ttf",
]

def _load_font(size: int, bold: bool = False):
    for p in (_FONT_BOLD_PATHS if bold else _FONT_REG_PATHS):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


# ── Database ────────────────────────────────────────────────────────
_stats_conn: _sql.Connection | None = None
_react_conn_ro: _sql.Connection | None = None
_stats_write_lock = asyncio.Lock()


def _matching_reaction_count(reactions, star_emoji: str) -> int:
    return sum(
        getattr(r, 'count', 0) or 0
        for r in reactions
        if str(getattr(r, 'emoji', '')) == star_emoji
        or getattr(getattr(r, 'emoji', None), 'name', None) == star_emoji
    )

def _get_conn() -> _sql.Connection:
    global _stats_conn
    if _stats_conn is None:
        _stats_conn = _sql.connect(SERVERSTATS_DB, check_same_thread=False)
        _stats_conn.execute("PRAGMA journal_mode=WAL")
        _stats_conn.execute("PRAGMA synchronous=NORMAL")
        _stats_conn.executescript("""
            CREATE TABLE IF NOT EXISTS message_counts (
                guild_id INTEGER NOT NULL,
                user_id  INTEGER NOT NULL,
                count    INTEGER DEFAULT 1,
                PRIMARY KEY (guild_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS vc_time (
                guild_id INTEGER NOT NULL,
                user_id  INTEGER NOT NULL,
                total_seconds INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS starboard_config (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                emoji TEXT NOT NULL DEFAULT '\u2b50',
                threshold INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS starboard_entries (
                guild_id    INTEGER NOT NULL,
                message_id  INTEGER NOT NULL,
                PRIMARY KEY (guild_id, message_id)
            )
        """)
        # migrate old starboard_config rows that lack emoji/threshold columns
        try:
            _stats_conn.executescript(
                "ALTER TABLE starboard_config ADD COLUMN emoji TEXT NOT NULL DEFAULT '\u2b50';"
            )
        except Exception:
            pass
        try:
            _stats_conn.executescript(
                "ALTER TABLE starboard_config ADD COLUMN threshold INTEGER NOT NULL DEFAULT 1;"
            )
        except Exception:
            pass
        _stats_conn.commit()
    return _stats_conn

def _get_react_conn_ro() -> _sql.Connection | None:
    """Cached read-only connection to reactions.db (best-effort — may not exist)."""
    global _react_conn_ro
    if _react_conn_ro is None:
        path = f"{DATA_DIR}/reactions.db"
        if not os.path.isfile(path):
            return None
        _react_conn_ro = _sql.connect(path)
        _react_conn_ro.execute("PRAGMA query_only = ON")
    return _react_conn_ro


# ── Image helpers ───────────────────────────────────────────────────
def _circle_avatar(img_bytes: bytes, size: int) -> Image.Image:
    im = Image.open(io.BytesIO(img_bytes)).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out

def _progress_ago(t: int) -> str:
    """Format seconds into a human-readable '1h 23m' or '2d 5h' string."""
    if t < 60:
        return f"{t}s"
    m, s = divmod(t, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m}m"
    d, h = divmod(h, 24)
    return f"{d}d {h}h"

def _format_vc_duration(total_seconds: int) -> str:
    """Format VC total seconds into 'Xh Ym' display string."""
    if total_seconds < 60:
        return f"{total_seconds}s"
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


# ── Emoji helpers (twemoji for unicode, Discord CDN for custom) ─────
def _emoji_to_url(emoji_str: str) -> str | None:
    """Resolve emoji string → CDN PNG URL."""
    if not emoji_str:
        return None
    m = re.match(r"<(a?):(\w+):(\d+)>", emoji_str)
    if m:
        ext = "gif" if m.group(1) else "png"
        return f"https://cdn.discordapp.com/emojis/{m.group(3)}.{ext}?size=96"
    parts = []
    for c in emoji_str:
        cp = ord(c)
        if cp == 0xFE0F:
            continue
        parts.append(f"{cp:x}")
    if not parts:
        return None
    return f"https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/{'-'.join(parts)}.png"

async def _fetch_emoji_bytes(url: str, session: aiohttp.ClientSession) -> bytes | None:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                return await r.read()
    except Exception:
        return None

# ── Server info card renderer ───────────────────────────────────────
def _render_server_card(
    icon_bytes: bytes | None,
    banner_bytes: bytes | None,
    guild_name: str,
    member_count: int,
    bot_count: int,
    boost_level: int,
    created_str: str,
    emoji_img_bytes: bytes | None,
    emoji_count_text: str,
    top_reactor_val: str,
    top_chatter_val: str,
    top_vc_val: str,
) -> io.BytesIO:
    W, H = 1000, 450

    if banner_bytes:
        base = Image.open(io.BytesIO(banner_bytes)).convert("RGB")
    elif icon_bytes:
        base = Image.open(io.BytesIO(icon_bytes)).convert("RGB")
    else:
        base = Image.new("RGB", (W, H), (20, 20, 25))
    bg = base.resize((W, H), Image.Resampling.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(35))
    bg = Image.blend(bg, Image.new("RGB", (W, H), (20, 20, 25)), 0.65)

    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        gd.line([(0, y), (W, y)], fill=(0, 0, 0, int(70 * (y / H))))
    bg = Image.alpha_composite(bg.convert("RGBA"), grad)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad = 30
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=30, fill=(255, 255, 255, 12))
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=30, outline=(255, 255, 255, 35), width=1)
    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    title_font   = _load_font(36, bold=True)
    stat_font    = _load_font(22, bold=False)
    small_font   = _load_font(18, bold=False)
    header_font  = _load_font(24, bold=True)
    label_font   = _load_font(18, bold=False)
    value_font   = _load_font(22, bold=True)

    # ── Left: guild icon + name ──
    icon_size = 110
    if icon_bytes:
        try:
            icon_img = Image.open(io.BytesIO(icon_bytes)).convert("RGBA").resize(
                (icon_size, icon_size), Image.Resampling.LANCZOS
            )
            m = Image.new("L", (icon_size, icon_size), 0)
            ImageDraw.Draw(m).rounded_rectangle((0, 0, icon_size, icon_size), radius=22, fill=255)
            icon_img.putalpha(m)
            bg.paste(icon_img, (55, 50), icon_img)
        except Exception:
            pass

    # Server name
    name_y = 50 + icon_size + 20
    draw.text((55, name_y), guild_name, font=title_font, fill=(255, 255, 255, 250))

    # Member / bot counts
    info_y = name_y + 50
    draw.text((55, info_y), f"Members: {member_count:,}", font=stat_font, fill=(255, 255, 255, 190))
    draw.text((55, info_y + 32), f"Bots: {bot_count:,}", font=stat_font, fill=(255, 255, 255, 170))
    draw.text((55, info_y + 64), f"Boost Level: {boost_level}", font=stat_font, fill=(255, 255, 255, 150))
    draw.text((55, info_y + 96), f"Created: {created_str}", font=small_font, fill=(255, 255, 255, 130))

    # Separator line
    sep_x = 340
    draw.line([(sep_x, 60), (sep_x, H - 50)], fill=(255, 255, 255, 40), width=1)

    # ── Right: top stats ──
    stat_x = 380
    stat_y = 70

    draw.text((stat_x, stat_y), "Top Stats", font=header_font, fill=(255, 255, 255, 230))
    draw.line([(stat_x, stat_y + 40), (W - 50, stat_y + 40)], fill=(255, 255, 255, 35), width=1)

    rows_y = stat_y + 65
    row_h = 70
    labels = ["Most Used Emoji", "Top Reactor", "Top Chatter", "Top VC User"]

    # Draw label rows
    for i in range(4):
        y = rows_y + i * row_h
        draw.text((stat_x, y), labels[i], font=label_font, fill=(255, 255, 255, 150))

    # Draw value rows
    # Row 0: emoji image + count text
    val_y0 = rows_y + 24
    if emoji_img_bytes and emoji_count_text:
        ew, eh = 28, 28
        try:
            ei = Image.open(io.BytesIO(emoji_img_bytes)).convert("RGBA").resize((ew, eh), Image.Resampling.LANCZOS)
            bg.paste(ei, (stat_x, val_y0), ei)
        except Exception:
            pass
        draw.text((stat_x + ew + 6, val_y0), emoji_count_text, font=value_font, fill=(255, 255, 255, 230))
    else:
        draw.text((stat_x, val_y0), "No data yet", font=value_font, fill=(255, 255, 255, 230))

    # Rows 1-3: plain text
    for i, val in enumerate([top_reactor_val, top_chatter_val, top_vc_val], start=1):
        y = rows_y + 24 + i * row_h
        draw.text((stat_x, y), val, font=value_font, fill=(255, 255, 255, 230))

    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG", quality=92)
    buf.seek(0)
    return buf


# ── Leaderboard card renderer ───────────────────────────────────────
def _render_lb_card(
    icon_bytes: bytes | None,
    title: str,
    subtitle: str,
    rows: list[tuple[int, str, int | str]],
    page_str: str,
    bot_avatar_bytes: bytes | None,
    bot_name: str,
    user_rank_text: str,
    unit: str = "",
) -> io.BytesIO:
    W, H = 900, 1100

    if icon_bytes:
        try:
            base = Image.open(io.BytesIO(icon_bytes)).convert("RGB")
        except Exception:
            base = Image.new("RGB", (W, H), (30, 30, 40))
    else:
        base = Image.new("RGB", (W, H), (30, 30, 40))
    bg = base.resize((W, H), Image.Resampling.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(45))
    bg = Image.blend(bg, Image.new("RGB", (W, H), (20, 20, 25)), 0.7)

    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        gd.line([(0, y), (W, y)], fill=(0, 0, 0, int(80 * (y / H))))
    bg = Image.alpha_composite(bg.convert("RGBA"), grad)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad = 50
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=35, fill=(255, 255, 255, 14))
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=35, outline=(255, 255, 255, 45), width=1)
    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    title_font    = _load_font(44, bold=True)
    subtitle_font = _load_font(24, bold=False)
    rank_font     = _load_font(30, bold=True)
    name_font     = _load_font(30, bold=False)
    count_font    = _load_font(30, bold=True)
    footer_font   = _load_font(20, bold=False)
    footer_bold   = _load_font(20, bold=True)

    draw.text((90, 80), title, font=title_font, fill=(255, 255, 255, 255))
    draw.text((90, 140), subtitle, font=subtitle_font, fill=(255, 255, 255, 170))
    draw.line([(90, 200), (W - 90, 200)], fill=(255, 255, 255, 60), width=1)

    start_y = 230
    row_h   = 60
    rank_x  = 90
    name_x  = 170
    count_x = W - 90

    tints = {
        1: (255, 215, 64, 255),
        2: (200, 200, 210, 255),
        3: (205, 127, 50, 255),
    }

    for i, (rank, name, count) in enumerate(rows):
        y = start_y + i * row_h
        rank_str = f"{rank}."
        rank_color = tints.get(rank, (255, 255, 255, 235))
        draw.text((rank_x, y), rank_str, font=rank_font, fill=rank_color)

        max_w = (count_x - 90) - name_x
        name_disp = name
        if draw.textbbox((0, 0), name_disp, font=name_font)[2] > max_w:
            while name_disp and draw.textbbox((0, 0), name_disp + "\u2026", font=name_font)[2] > max_w:
                name_disp = name_disp[:-1]
            name_disp = (name_disp + "\u2026") if name_disp else "\u2026"
        draw.text((name_x, y), name_disp, font=name_font, fill=(255, 255, 255, 220))

        if isinstance(count, str):
            count_str = count
        else:
            count_str = f"{count:,}{unit}"
        cw = draw.textbbox((0, 0), count_str, font=count_font)[2]
        draw.text((count_x - cw, y), count_str, font=count_font, fill=rank_color)

    footer_y = H - 130
    draw.line([(90, footer_y), (W - 90, footer_y)], fill=(255, 255, 255, 50), width=1)

    av_size = 40
    av_x, av_y = 90, footer_y + 22
    if bot_avatar_bytes:
        try:
            av = _circle_avatar(bot_avatar_bytes, av_size)
            bg.paste(av, (av_x, av_y), av)
        except Exception:
            pass
    text_x = av_x + av_size + 14
    draw.text((text_x, av_y - 2), bot_name, font=footer_bold, fill=(255, 255, 255, 230))
    draw.text((text_x, av_y + 22), user_rank_text, font=footer_font, fill=(255, 255, 255, 160))

    pw = draw.textbbox((0, 0), page_str, font=footer_font)[2]
    draw.text((W - 90 - pw, av_y + 8), page_str, font=footer_font, fill=(255, 255, 255, 160))

    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG", quality=92)
    buf.seek(0)
    return buf


# ── Paginated leaderboard view ──────────────────────────────────────
class LBPageView(discord.ui.View):
    def __init__(self, bot, ctx, rows, title, subtitle, unit="", per_page=10):
        super().__init__(timeout=120)
        self.bot = bot
        self.ctx = ctx
        self.rows = rows
        self.title = title
        self.subtitle = subtitle
        self.unit = unit
        self.per_page = per_page
        self.page = 0
        self.total = max(1, (len(rows) - 1) // per_page + 1)
        self.icon_bytes: bytes | None = None
        self.bot_avatar_bytes: bytes | None = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # only the command author may page through this leaderboard
        return interaction.user.id == self.ctx.author.id

    async def fetch_assets(self):
        async with aiohttp.ClientSession() as s:
            tasks = [
                self._fetch(s, self.bot.user.display_avatar.url),
                self._fetch(s, self.ctx.author.display_avatar.url),
            ]
            results = await asyncio.gather(*tasks)
            self.icon_bytes = results[0]
            self.bot_avatar_bytes = results[1]

    async def _fetch(self, session: aiohttp.ClientSession, url: str) -> bytes | None:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    return await r.read()
        except Exception:
            return None

    def _resolve_name(self, user_id: int) -> str:
        if self.ctx.guild:
            m = self.ctx.guild.get_member(user_id)
            if m:
                return m.display_name
        return f"user-{user_id}"

    def _user_rank_text(self) -> str:
        rank = next(
            (i + 1 for i, (uid, _) in enumerate(self.rows) if uid == self.ctx.author.id),
            None,
        )
        if rank is None:
            return "unranked"
        count = self.rows[rank - 1][1]
        if isinstance(count, str):
            return f"#{rank} \u00b7 {count}"
        unit = f" {self.unit.strip()}" if self.unit else ""
        return f"#{rank} \u00b7 {count:,}{unit}"

    async def render_file(self) -> discord.File:
        start = self.page * self.per_page
        chunk = self.rows[start:start + self.per_page]
        named = [(rank, self._resolve_name(uid), count)
                 for rank, (uid, count) in enumerate(chunk, start=start + 1)]
        page_str = f"page {self.page + 1}/{self.total}  \u00b7  {len(self.rows)} ranked"
        buf = await asyncio.to_thread(
            _render_lb_card,
            self.icon_bytes,
            self.title,
            self.subtitle,
            named,
            page_str,
            self.bot_avatar_bytes,
            self.ctx.author.display_name,
            self._user_rank_text(),
            self.unit,
        )
        return discord.File(fp=buf, filename="leaderboard.png")

    async def _refresh(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            file = await self.render_file()
            await interaction.message.edit(attachments=[file], view=self)
        except Exception as e:
            log.warning("leaderboard refresh failed: %s", e)

    @discord.ui.button(label="\u25c0", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, _btn):
        if self.page > 0:
            self.page -= 1
            await self._refresh(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="\u25b6", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _btn):
        if self.page + 1 < self.total:
            self.page += 1
            await self._refresh(interaction)
        else:
            await interaction.response.defer()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


# ── Emoji usage leaderboard (reaction_stats based) ──────────────────
_emoji_img_cache: dict[str, bytes] = {}
_EMOJI_CACHE_MAX = 200


def _render_emoji_card(
    icon_bytes: bytes | None,
    title: str,
    subtitle: str,
    rows: list[tuple[int, str, bytes | None, int]],
    page_str: str,
    bot_avatar_bytes: bytes | None,
    user_rank_text: str,
) -> io.BytesIO:
    W, H = 900, 1100
    if icon_bytes:
        try:
            base = Image.open(io.BytesIO(icon_bytes)).convert("RGB")
        except Exception:
            base = Image.new("RGB", (W, H), (30, 30, 40))
    else:
        base = Image.new("RGB", (W, H), (30, 30, 40))
    bg = base.resize((W, H), Image.Resampling.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(45))
    bg = Image.blend(bg, Image.new("RGB", (W, H), (20, 20, 25)), 0.7)

    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        gd.line([(0, y), (W, y)], fill=(0, 0, 0, int(80 * (y / H))))
    bg = Image.alpha_composite(bg.convert("RGBA"), grad)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad = 50
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=35, fill=(255, 255, 255, 14))
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=35, outline=(255, 255, 255, 45), width=1)
    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    title_font    = _load_font(44, bold=True)
    subtitle_font = _load_font(24, bold=False)
    rank_font     = _load_font(30, bold=True)
    name_font     = _load_font(30, bold=False)
    count_font    = _load_font(30, bold=True)
    footer_font   = _load_font(20, bold=False)
    footer_bold   = _load_font(20, bold=True)

    draw.text((90, 80), title, font=title_font, fill=(255, 255, 255, 255))
    draw.text((90, 140), subtitle, font=subtitle_font, fill=(255, 255, 255, 170))
    draw.line([(90, 200), (W - 90, 200)], fill=(255, 255, 255, 60), width=1)

    start_y = 230
    row_h   = 70
    rank_x  = 90
    emoji_x = 170
    name_x  = 250
    count_x = W - 90

    tints = {
        1: (255, 215, 64, 255),
        2: (200, 200, 210, 255),
        3: (205, 127, 50, 255),
    }

    for i, (rank, name, img_bytes, count) in enumerate(rows):
        y = start_y + i * row_h
        rank_color = tints.get(rank, (255, 255, 255, 235))
        draw.text((rank_x, y), f"{rank}.", font=rank_font, fill=rank_color)

        if img_bytes:
            try:
                e = _circle_avatar(img_bytes, 44)
                bg.paste(e, (emoji_x, y - 2), e)
            except Exception:
                pass

        name_disp = name
        max_w = (count_x - 90) - name_x
        if draw.textbbox((0, 0), name_disp, font=name_font)[2] > max_w:
            while name_disp and draw.textbbox((0, 0), name_disp + "\u2026", font=name_font)[2] > max_w:
                name_disp = name_disp[:-1]
            name_disp = (name_disp + "\u2026") if name_disp else "\u2026"
        draw.text((name_x, y), name_disp, font=name_font, fill=(255, 255, 255, 220))

        count_str = f"{count:,} uses"
        cw = draw.textbbox((0, 0), count_str, font=count_font)[2]
        draw.text((count_x - cw, y), count_str, font=count_font, fill=rank_color)

    footer_y = H - 130
    draw.line([(90, footer_y), (W - 90, footer_y)], fill=(255, 255, 255, 50), width=1)
    if bot_avatar_bytes:
        try:
            av = _circle_avatar(bot_avatar_bytes, 40)
            bg.paste(av, (90, footer_y + 22), av)
        except Exception:
            pass
    draw.text((144, footer_y + 20), user_rank_text, font=footer_font, fill=(255, 255, 255, 160))

    pw = draw.textbbox((0, 0), page_str, font=footer_font)[2]
    draw.text((W - 90 - pw, footer_y + 28), page_str, font=footer_font, fill=(255, 255, 255, 160))

    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG", quality=92)
    buf.seek(0)
    return buf


def _emoji_display_name(emoji_str: str) -> str:
    m = re.match(r"<(a?):(\w+):\d+>", emoji_str)
    if m:
        return f":{m.group(2)}:"
    return emoji_str


class EmojiLBView(discord.ui.View):
    def __init__(self, bot, ctx, emoji_counts: list[tuple[str, int]], title, subtitle, per_page=10):
        super().__init__(timeout=120)
        self.bot = bot
        self.ctx = ctx
        self.emoji_counts = emoji_counts
        self.title = title
        self.subtitle = subtitle
        self.per_page = per_page
        self.page = 0
        self.total = max(1, (len(emoji_counts) - 1) // per_page + 1)
        self.icon_bytes: bytes | None = None
        self.bot_avatar_bytes: bytes | None = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.author.id

    async def fetch_assets(self):
        async with aiohttp.ClientSession() as s:
            tasks = [
                self._fetch(s, self.bot.user.display_avatar.url),
                self._fetch(s, self.ctx.author.display_avatar.url),
            ]
            results = await asyncio.gather(*tasks)
            self.icon_bytes = results[0]
            self.bot_avatar_bytes = results[1]

    async def _fetch(self, session: aiohttp.ClientSession, url: str) -> bytes | None:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    return await r.read()
        except Exception:
            return None

    async def _emoji_images(self, page_emojis: list[str]) -> dict[str, bytes | None]:
        out: dict[str, bytes | None] = {}
        need: list[str] = []
        for e in page_emojis:
            if e in _emoji_img_cache:
                out[e] = _emoji_img_cache[e]
            else:
                need.append(e)
        if need:
            async with aiohttp.ClientSession() as s:
                async def _get(e):
                    url = _emoji_to_url(e)
                    if not url:
                        return e, None
                    b = await _fetch_emoji_bytes(url, s)
                    return e, b
                for e, b in await asyncio.gather(*[_get(e) for e in need]):
                    out[e] = b
                    if b and len(_emoji_img_cache) < _EMOJI_CACHE_MAX:
                        _emoji_img_cache[e] = b
        return out

    async def render_file(self) -> discord.File:
        start = self.page * self.per_page
        chunk = self.emoji_counts[start:start + self.per_page]
        imgs = await self._emoji_images([e for e, _ in chunk])
        named = [
            (rank, _emoji_display_name(e), imgs.get(e), count)
            for rank, (e, count) in enumerate(chunk, start=start + 1)
        ]
        page_str = f"page {self.page + 1}/{self.total}  \u00b7  {len(self.emoji_counts)} emoji"
        rank_text = f"top {len(self.emoji_counts)} most reacted emoji in /{self.ctx.guild.name}"
        buf = await asyncio.to_thread(
            _render_emoji_card,
            self.icon_bytes,
            self.title,
            self.subtitle,
            named,
            page_str,
            self.bot_avatar_bytes,
            rank_text,
        )
        return discord.File(fp=buf, filename="emoji_leaderboard.png")

    async def _refresh(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            file = await self.render_file()
            await interaction.message.edit(attachments=[file], view=self)
        except Exception as e:
            log.warning("emoji leaderboard refresh failed: %s", e)

    @discord.ui.button(label="\u25c0", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, _btn):
        if self.page > 0:
            self.page -= 1
            await self._refresh(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="\u25b6", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _btn):
        if self.page + 1 < self.total:
            self.page += 1
            await self._refresh(interaction)
        else:
            await interaction.response.defer()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


# ── Cog ─────────────────────────────────────────────────────────────
COG_META = {
    "category": "general",
    "label": "Server Stats",
    "desc": "Server info card, message & VC leaderboards.",
}

class ServerStatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._vc_join_times: dict[int, dict[int, float]] = {}

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        self._vc_join_times.pop(guild.id, None)

    async def cog_load(self):
        _get_conn()
        # bot.guilds is empty until the gateway connects (cog_load runs before
        # bot.start) — seed VC join times as a background task after ready,
        # otherwise members already in VC at restart silently lose their session
        self._vc_seed_task = asyncio.create_task(self._seed_vc_join_times())

    async def _seed_vc_join_times(self) -> None:
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                for member in vc.members:
                    if not member.bot:
                        self._vc_join_times.setdefault(guild.id, {})[member.id] = time.time()

    def cog_unload(self) -> None:
        if getattr(self, "_vc_seed_task", None) and not self._vc_seed_task.done():
            self._vc_seed_task.cancel()

    # ── Listeners ────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        async with _stats_write_lock:
            conn = _get_conn()
            conn.execute(
                "INSERT INTO message_counts (guild_id, user_id, count) VALUES (?, ?, 1) "
                "ON CONFLICT(guild_id, user_id) DO UPDATE SET count = count + 1",
                (message.guild.id, message.author.id),
            )
            conn.commit()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return
        guild_id = member.guild.id
        user_id = member.id

        if before.channel is None and after.channel is not None:
            self._vc_join_times.setdefault(guild_id, {})[user_id] = time.time()
        elif after.channel is None or before.channel != after.channel:
            joins = self._vc_join_times.get(guild_id, {})
            if user_id in joins:
                elapsed = int(time.time() - joins.pop(user_id))
                if elapsed > 0:
                    async with _stats_write_lock:
                        conn = _get_conn()
                        conn.execute(
                            "INSERT INTO vc_time (guild_id, user_id, total_seconds) VALUES (?, ?, ?) "
                            "ON CONFLICT(guild_id, user_id) DO UPDATE SET total_seconds = total_seconds + ?",
                            (guild_id, user_id, elapsed, elapsed),
                        )
                        conn.commit()
            if after.channel is not None:
                self._vc_join_times.setdefault(guild_id, {})[user_id] = time.time()

    # ── Data helpers ─────────────────────────────────────────────────
    @staticmethod
    def _get_msg_top(guild_id: int, limit: int = 1):
        conn = _get_conn()
        return conn.execute(
            "SELECT user_id, count FROM message_counts WHERE guild_id = ? ORDER BY count DESC LIMIT ?",
            (guild_id, limit),
        ).fetchall()

    @staticmethod
    def _get_vc_top(guild_id: int, limit: int = 1):
        conn = _get_conn()
        return conn.execute(
            "SELECT user_id, total_seconds FROM vc_time WHERE guild_id = ? ORDER BY total_seconds DESC LIMIT ?",
            (guild_id, limit),
        ).fetchall()

    @staticmethod
    def _get_emoji_top(guild_id: int):
        try:
            conn = _get_react_conn_ro()
            if conn is None:
                return None
            return conn.execute(
                "SELECT emoji, SUM(count) as total FROM reaction_stats "
                "WHERE guild_id = ? GROUP BY emoji ORDER BY total DESC LIMIT 1",
                (guild_id,),
            ).fetchone()
        except Exception:
            return None

    @staticmethod
    def _get_top_reactor(guild_id: int):
        try:
            conn = _get_react_conn_ro()
            if conn is None:
                return None
            return conn.execute(
                "SELECT user_id, SUM(count) as total FROM reaction_stats "
                "WHERE guild_id = ? GROUP BY user_id ORDER BY total DESC LIMIT 1",
                (guild_id,),
            ).fetchone()
        except Exception:
            return None

    def _get_msg_total(self, guild_id: int) -> int:
        row = _get_conn().execute(
            "SELECT SUM(count) FROM message_counts WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        return row[0] or 0 if row else 0

    # ── Starboard ─────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return

        conn = _get_conn()
        row = conn.execute(
            "SELECT channel_id, emoji, threshold FROM starboard_config WHERE guild_id = ?",
            (payload.guild_id,),
        ).fetchone()
        if row is None:
            return
        channel_id, star_emoji, threshold = row

        if str(payload.emoji) != star_emoji and payload.emoji.name != star_emoji:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        channel = guild.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            msg = await channel.fetch_message(payload.message_id)
        except Exception:
            return

        star_count = _matching_reaction_count(msg.reactions, star_emoji)
        if star_count < threshold:
            return

        # Atomically claim this message for starboarding (prevents duplicates)
        async with _stats_write_lock:
            before = conn.total_changes
            conn.execute(
                "INSERT OR IGNORE INTO starboard_entries (guild_id, message_id) VALUES (?, ?)",
                (payload.guild_id, payload.message_id),
            )
            conn.commit()
            if conn.total_changes == before:
                return  # Another reaction already handled this message

        star_channel = guild.get_channel(channel_id)
        if star_channel is None:
            return

        embed = discord.Embed(
            description=msg.content or "\u200b",
            color=discord.Color.gold(),
            timestamp=msg.created_at,
        )
        embed.set_author(
            name=msg.author.display_name,
            icon_url=msg.author.display_avatar.url,
        )
        if msg.attachments:
            first = msg.attachments[0]
            if first.content_type and first.content_type.startswith("image/"):
                embed.set_image(url=first.url)
        embed.add_field(
            name="Source",
            value=f"[jump to message]({msg.jump_url}) in #{channel.name}",
            inline=False,
        )
        embed.set_footer(text=f"{star_emoji} {star_count} \u00b7 {msg.author.id}")

        await star_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @help_meta(
        usage="`.starboard #channel [emoji] [threshold]`\n`.starboard emoji 😭`\n`.starboard threshold 5`",
        desc="Manages the starboard: set channel, emoji, and reaction threshold.",
        staff=True,
        examples=[".starboard #starboard ⭐ 3", ".starboard emoji 😭", ".starboard threshold 5"],
        params=[
            {"name": "channel", "type": "discord.TextChannel", "required": False, "desc": "The channel to use as starboard."},
            {"name": "emoji", "type": "str", "required": False, "desc": "Reaction emoji to track."},
            {"name": "threshold", "type": "int", "required": False, "desc": "Number of reactions required to appear on starboard."},
        ],
        note="Admin only. Subcommands: `emoji`, `threshold`.",
    )
    @commands.group(name="starboard", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def starboard(self, ctx: commands.Context, channel: discord.TextChannel = None, emoji: str = None, threshold: int = None):
        if channel is None:
            return await ctx.send("-# `.starboard #channel [emoji] [threshold]` — or `.starboard emoji 😭` / `.starboard threshold 5`")
        conn = _get_conn()
        existing = conn.execute(
            "SELECT emoji, threshold FROM starboard_config WHERE guild_id = ?",
            (ctx.guild.id,),
        ).fetchone()
        final_emoji = emoji or (existing[0] if existing else "\u2b50")
        final_threshold = threshold if threshold is not None else (existing[1] if existing else 1)
        async with _stats_write_lock:
            conn.execute(
                "INSERT OR REPLACE INTO starboard_config (guild_id, channel_id, emoji, threshold) VALUES (?, ?, ?, ?)",
                (ctx.guild.id, channel.id, final_emoji, final_threshold),
            )
            conn.commit()
        await ctx.send(f"\u2b50 Starboard set to {channel.mention} \u00b7 emoji: {final_emoji} \u00b7 threshold: {final_threshold}")

    @help_meta(
        usage=".starboard emoji <emoji>",
        desc="Changes the reaction emoji the starboard watches for.",
        staff=True,
        examples=[".starboard emoji ⭐", ".starboard emoji 😭"],
        params=[
            {"name": "emoji", "type": "str", "required": True, "desc": "The emoji to track for starboard entries."},
        ],
        note="Admin only. A starboard channel must be set first.",
    )
    @starboard.command(name="emoji")
    @commands.has_permissions(administrator=True)
    async def starboard_emoji(self, ctx: commands.Context, emoji: str):
        conn = _get_conn()
        existing = conn.execute(
            "SELECT channel_id, threshold FROM starboard_config WHERE guild_id = ?",
            (ctx.guild.id,),
        ).fetchone()
        if not existing:
            return await ctx.send("set a channel first with `.starboard #channel`")
        channel_id, threshold = existing
        async with _stats_write_lock:
            conn.execute(
                "INSERT OR REPLACE INTO starboard_config (guild_id, channel_id, emoji, threshold) VALUES (?, ?, ?, ?)",
                (ctx.guild.id, channel_id, emoji, threshold),
            )
            conn.commit()
        await ctx.send(f"\u2b50 Starboard emoji changed to {emoji}")

    @help_meta(
        usage=".starboard threshold <number>",
        desc="Sets the minimum reactions needed for a message to appear on the starboard.",
        staff=True,
        examples=[".starboard threshold 3", ".starboard threshold 5"],
        params=[
            {"name": "threshold", "type": "int", "required": True, "desc": "Minimum reaction count (must be at least 1)."},
        ],
        note="Admin only. A starboard channel must be set first.",
    )
    @starboard.command(name="threshold")
    @commands.has_permissions(administrator=True)
    async def starboard_threshold(self, ctx: commands.Context, threshold: int):
        if threshold < 1:
            return await ctx.send("threshold must be at least 1")
        conn = _get_conn()
        existing = conn.execute(
            "SELECT channel_id, emoji FROM starboard_config WHERE guild_id = ?",
            (ctx.guild.id,),
        ).fetchone()
        if not existing:
            return await ctx.send("set a channel first with `.starboard #channel`")
        channel_id, emoji = existing
        async with _stats_write_lock:
            conn.execute(
                "INSERT OR REPLACE INTO starboard_config (guild_id, channel_id, emoji, threshold) VALUES (?, ?, ?, ?)",
                (ctx.guild.id, channel_id, emoji, threshold),
            )
            conn.commit()
        await ctx.send(f"\u2b50 Starboard threshold set to {threshold}")

    # ── Commands ───────────────────────────────────────────────────────────
    @help_meta(
        usage="`.seoulities`",
        desc="Shows a server info card with member stats, top emoji, top reactor, top chatter, and top VC user.",
        examples=[".seoulities"],
        params=[],
        note="Cooldown: 15s per channel. Generates an info card image.",
    )
    @commands.command(name="seoulities")
    @commands.cooldown(1, 15, commands.BucketType.channel)
    async def seoulities(self, ctx: commands.Context):
        guild = ctx.guild
        if not guild:
            return await ctx.send("This command only works in a server.")

        async with ctx.typing():
            icon_b = None
            banner_b = None
            try:
                if guild.icon:
                    icon_b = await guild.icon.read()
                if guild.banner:
                    banner_b = await guild.banner.read()
            except Exception:
                pass

            member_count = guild.member_count or len(guild.members)
            bot_count = sum(1 for m in guild.members if m.bot)
            boost_level = guild.premium_tier
            created_str = guild.created_at.strftime("%b %d, %Y")

            emoji_data = self._get_emoji_top(guild.id)
            reactor_data = self._get_top_reactor(guild.id)
            chatter_row = self._get_msg_top(guild.id, 1)
            vc_row = self._get_vc_top(guild.id, 1)

            def _name(uid: int) -> str:
                m = guild.get_member(uid)
                return m.display_name if m else f"<@{uid}>"

            # Fetch top emoji as twemoji PNG
            emoji_img_bytes = None
            emoji_count_text = "No data yet"
            async with aiohttp.ClientSession() as s:
                if emoji_data:
                    emoji_img_url = _emoji_to_url(emoji_data[0])
                    if emoji_img_url:
                        emoji_img_bytes = await _fetch_emoji_bytes(emoji_img_url, s)
                    emoji_count_text = f"({emoji_data[1]:,} uses)"

            top_reactor_val = f"{_name(reactor_data[0])}  ({reactor_data[1]:,} reactions)" if reactor_data else "No data yet"
            top_chatter_val = f"{_name(chatter_row[0][0])}  ({chatter_row[0][1]:,} msgs)" if chatter_row else "No data yet"
            top_vc_val = f"{_name(vc_row[0][0])}  ({_progress_ago(vc_row[0][1])})" if vc_row else "No data yet"

            buf = await asyncio.to_thread(
                _render_server_card,
                icon_b,
                banner_b,
                guild.name,
                member_count,
                bot_count,
                boost_level,
                created_str,
                emoji_img_bytes,
                emoji_count_text,
                top_reactor_val,
                top_chatter_val,
                top_vc_val,
            )
        file = discord.File(fp=buf, filename="seoulities.png")
        await ctx.send(file=file)

    @help_meta(
        usage="`.lb`  ·  `.lb messages`  ·  `.lb vctime`",
        desc="Leaderboards for messages and VC time.",
        section="Leaderboards",
        examples=[".lb", ".lb messages", ".lb vctime"],
        params=[],
        note="Subcommands: `messages`, `vctime`.",
    )
    @commands.group(name="leaderboard", aliases=["lb"])
    @commands.cooldown(1, 10, commands.BucketType.channel)
    async def leaderboard(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send("-# `.lb messages` — top 200 message senders · `.lb vctime` — voice time")

    @help_meta(
        usage="`.lb messages`",
        desc="Shows the top 200 message senders in the server.",
        section="Leaderboards",
        examples=[".lb messages"],
        params=[],
        note="Updates in real-time as messages are sent.",
    )
    @leaderboard.command(name="messages", aliases=["msgs", "msg", "lbm"])
    async def lb_messages(self, ctx: commands.Context):
        rows = self._get_msg_top(ctx.guild.id, 200)
        if not rows:
            return await ctx.send("No message data yet.")
        view = LBPageView(
            self.bot, ctx, rows,
            title="Message Leaderboard",
            subtitle=f"most messages in /{ctx.guild.name}",
            unit=" msgs",
        )
        await view.fetch_assets()
        file = await view.render_file()
        view.message = await ctx.send(file=file, view=view)

    @help_meta(
        usage="`.lb vctime`",
        desc="Shows the top 200 VC time leaders in the server.",
        section="Leaderboards",
        examples=[".lb vctime"],
        params=[],
        note="Tracks time spent in voice channels.",
    )
    @leaderboard.command(name="vctime", aliases=["vc", "voice", "lbv"])
    async def lb_vctime(self, ctx: commands.Context):
        raw = self._get_vc_top(ctx.guild.id, 200)
        if not raw:
            return await ctx.send("No VC time data yet.")
        rows = [(uid, _format_vc_duration(secs)) for uid, secs in raw]
        view = LBPageView(
            self.bot, ctx, rows,
            title="VC Time Leaderboard",
            subtitle=f"most time in voice in /{ctx.guild.name}",
            unit="",
        )
        await view.fetch_assets()
        file = await view.render_file()
        view.message = await ctx.send(file=file, view=view)

    @help_meta(
        usage="`.lb emojis`",
        desc="Shows the most used emoji in the server, from reaction stats.",
        section="Leaderboards",
        examples=[".lb emojis"],
        params=[],
        note="counts every reaction added in the server.",
    )
    @leaderboard.command(name="emojis", aliases=["emj", "emoji", "lbe"])
    async def lb_emojis(self, ctx: commands.Context):
        try:
            conn = _get_react_conn_ro()
        except Exception:
            return await ctx.send("-# no reaction data yet.")
        if conn is None:
            return await ctx.send("-# no reaction data yet.")
        rows = conn.execute(
            "SELECT emoji, SUM(count) as total FROM reaction_stats "
            "WHERE guild_id = ? GROUP BY emoji ORDER BY total DESC LIMIT 30",
            (ctx.guild.id,),
        ).fetchall()
        if not rows:
            return await ctx.send("-# no reaction data yet. go react to stuff.")
        view = EmojiLBView(
            self.bot, ctx, [(e, c) for e, c in rows],
            title="Emoji Leaderboard",
            subtitle=f"most used emoji in /{ctx.guild.name}",
        )
        await view.fetch_assets()
        file = await view.render_file()
        view.message = await ctx.send(file=file, view=view)

    @help_meta(
        usage="`.lb birthdays`",
        desc="Shows birthdays coming up this month in the server.",
        section="Leaderboards",
        examples=[".lb birthdays"],
        params=[],
        note="from the birthday registry (.bday).",
    )
    @leaderboard.command(name="birthdays", aliases=["bdays", "bdaylb"])
    async def lb_birthdays(self, ctx: commands.Context):
        from datetime import datetime
        from cogs.reminders import BIRTHDAYS_FILE
        from utils import load_json

        now = datetime.now()
        month = now.strftime("%m")
        rows = []
        for it in (load_json(BIRTHDAYS_FILE) or {}).get("items", []):
            md = str(it.get("month_day", ""))
            if len(md) != 5 or md[2] != "-":
                continue
            if md[:2] != month:
                continue
            try:
                label = datetime.strptime(md, "%m-%d").strftime("%b %d")
            except ValueError:
                continue
            rows.append((int(it["user_id"]), label))
        if not rows:
            return await ctx.send("-# no birthdays this month. check back later.")
        rows.sort(key=lambda x: x[1])
        view = LBPageView(
            self.bot, ctx, rows,
            title="Birthdays This Month",
            subtitle=f"upcoming birthdays in /{ctx.guild.name}",
            unit="",
        )
        await view.fetch_assets()
        file = await view.render_file()
        view.message = await ctx.send(file=file, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerStatsCog(bot))
