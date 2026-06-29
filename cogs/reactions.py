import asyncio
import io
import logging
import re
import sqlite3 as _sql

import aiohttp
import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from utils import DATA_DIR, get_embed_color, help_meta, is_owner_or_creator

log = logging.getLogger(__name__)

# ── cogs/reactions.py ───────────────────────────────────────────
COG_META = {
    "category": "reactions",
    "label": "Reactions",
    "desc": "Core utility and reaction commands.",
}


REACTIONS_DB = f"{DATA_DIR}/reactions.db"
_react_conn: _sql.Connection | None = None

def _get_react_conn() -> _sql.Connection:
    global _react_conn
    if _react_conn is None:
        _react_conn = _sql.connect(REACTIONS_DB, check_same_thread=False)
        _react_conn.execute("PRAGMA journal_mode=WAL")
        _react_conn.execute("PRAGMA synchronous=NORMAL")
        _react_conn.execute("""
            CREATE TABLE IF NOT EXISTS reaction_stats (
                guild_id INTEGER NOT NULL,
                user_id  INTEGER NOT NULL,
                emoji    TEXT    NOT NULL,
                count    INTEGER DEFAULT 1,
                PRIMARY KEY (guild_id, user_id, emoji)
            )
        """)
        _react_conn.commit()
    return _react_conn

_get_react_conn()  # init on import

def _rc_upsert(guild_id, user_id, emoji_str, delta):
    conn = _get_react_conn()
    cx = conn.cursor()
    cx.execute("""
        INSERT OR IGNORE INTO reaction_stats (guild_id, user_id, emoji, count)
        VALUES (?, ?, ?, 0)
    """, (guild_id, user_id, emoji_str))
    cx.execute("""
        UPDATE reaction_stats SET count = MAX(0, count + ?)
        WHERE guild_id = ? AND user_id = ? AND emoji = ?
    """, (delta, guild_id, user_id, emoji_str))
    conn.commit()

def _emoji_str(emoji):
    emoji_id = getattr(emoji, 'id', None)
    emoji_name = getattr(emoji, 'name', None)
    if emoji_id and emoji_name:
        prefix = 'a' if getattr(emoji, 'animated', False) else ''
        return f"<{prefix}:{emoji_name}:{emoji_id}>"
    return str(emoji)


# ── Leaderboard image renderer (music-card aesthetic) ──────────

# JetBrains Mono preferred, fallback chain for cross-platform compatibility.
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


def _emoji_to_url(emoji_str: str) -> str | None:
    """Resolve emoji string → CDN PNG URL.
    - Custom Discord emoji <:name:id> → cdn.discordapp.com
    - Unicode emoji → Twemoji jsdelivr"""
    if not emoji_str:
        return None
    m = re.match(r"<(a?):(\w+):(\d+)>", emoji_str)
    if m:
        ext = "gif" if m.group(1) else "png"
        return f"https://cdn.discordapp.com/emojis/{m.group(3)}.{ext}?size=96"
    # unicode → twemoji codepoints (strip variation selector U+FE0F which twemoji omits)
    parts = []
    for c in emoji_str:
        cp = ord(c)
        if cp == 0xFE0F:
            continue
        parts.append(f"{cp:x}")
    if not parts:
        return None
    return f"https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/{'-'.join(parts)}.png"


async def _fetch_image_bytes(url: str, session: aiohttp.ClientSession | None = None) -> bytes | None:
    """Download an image (avatar/icon/emoji). Returns None on any failure."""
    own = session is None
    if own:
        session = aiohttp.ClientSession()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                return await r.read()
    except Exception as e:
        log.warning(f"image fetch failed for {url}: {e}")
    finally:
        if own:
            await session.close()
    return None


def _circle_avatar(img_bytes: bytes, size: int) -> Image.Image:
    """Crop image to a circle of given size."""
    im = Image.open(io.BytesIO(img_bytes)).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def _render_leaderboard_card(
    icon_bytes: bytes | None,
    title: str,
    subtitle: str,
    rows: list[tuple[int, str, int | str]],
    page_str: str,
    bot_avatar_bytes: bytes | None,
    bot_name: str,
    user_rank_text: str,
    title_emoji_bytes: bytes | None = None,
) -> io.BytesIO:
    """CPU-intensive image render — call via asyncio.to_thread.
    Tall card with blurred guild-icon background, glass overlay, and a
    bot-avatar + user-rank footer."""
    W, H = 900, 1100

    # ── Background: blurred guild icon (or fallback gradient) ──
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

    # gradient overlay top→bottom
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        gd.line([(0, y), (W, y)], fill=(0, 0, 0, int(80 * (y / H))))
    bg = Image.alpha_composite(bg.convert("RGBA"), grad)

    # ── Glass card overlay ──
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

    # ── Title block (with optional emoji image inline) ──
    title_x = 90
    title_y = 80
    if title_emoji_bytes:
        try:
            ei = Image.open(io.BytesIO(title_emoji_bytes)).convert("RGBA").resize((52, 52), Image.Resampling.LANCZOS)
            bg.paste(ei, (title_x, title_y - 4), ei)
            title_x += 64
        except Exception:
            pass
    draw.text((title_x, title_y),  title,    font=title_font,    fill=(255, 255, 255, 255))
    draw.text((90,      title_y + 60), subtitle, font=subtitle_font, fill=(255, 255, 255, 170))

    draw.line([(90, 200), (W - 90, 200)], fill=(255, 255, 255, 60), width=1)

    # ── Rows ──
    start_y = 230
    row_h   = 60
    rank_x  = 90
    name_x  = 170
    count_x = W - 90

    tints = {
        1: (255, 215, 64,  255),
        2: (200, 200, 210, 255),
        3: (205, 127, 50,  255),
    }

    for i, (rank, name, count) in enumerate(rows):
        y = start_y + i * row_h
        rank_str = f"{rank}."
        rank_color = tints.get(rank, (255, 255, 255, 235))
        draw.text((rank_x, y), rank_str, font=rank_font, fill=rank_color)

        max_w = (count_x - 90) - name_x
        name_disp = name
        if draw.textbbox((0, 0), name_disp, font=name_font)[2] > max_w:
            while name_disp and draw.textbbox((0, 0), name_disp + "…", font=name_font)[2] > max_w:
                name_disp = name_disp[:-1]
            name_disp = (name_disp + "…") if name_disp else "…"
        draw.text((name_x, y), name_disp, font=name_font, fill=(255, 255, 255, 220))

        if isinstance(count, str):
            count_str = count
        else:
            count_str = f"{count:,}"
        cw = draw.textbbox((0, 0), count_str, font=count_font)[2]
        draw.text((count_x - cw, y), count_str, font=count_font, fill=rank_color)

    # ── Footer area ──
    footer_y = H - 130
    draw.line([(90, footer_y), (W - 90, footer_y)], fill=(255, 255, 255, 50), width=1)

    # bot avatar circle on the left
    av_size = 40
    av_x, av_y = 90, footer_y + 22
    if bot_avatar_bytes:
        try:
            av = _circle_avatar(bot_avatar_bytes, av_size)
            bg.paste(av, (av_x, av_y), av)
        except Exception:
            pass
    # bot name + user rank
    text_x = av_x + av_size + 14
    draw.text((text_x, av_y - 2), bot_name, font=footer_bold, fill=(255, 255, 255, 230))
    draw.text((text_x, av_y + 22), user_rank_text, font=footer_font, fill=(255, 255, 255, 160))

    # page info on the right
    pw = draw.textbbox((0, 0), page_str, font=footer_font)[2]
    draw.text((W - 90 - pw, av_y + 8), page_str, font=footer_font, fill=(255, 255, 255, 160))

    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG", quality=92)
    buf.seek(0)
    return buf


class RCImageView(discord.ui.View):
    """Paginated leaderboard rendered as an image (music-card aesthetic)."""

    def __init__(self, bot, ctx, rows, title, subtitle, accent, per_page=10,
                 emoji_str: str | None = None):
        super().__init__(timeout=120)
        self.bot = bot
        self.ctx = ctx
        self.rows = rows
        self.title = title
        self.subtitle = subtitle
        self.accent = accent
        self.per_page = per_page
        self.emoji_str = emoji_str
        self.page = 0
        self.total = max(1, (len(rows) - 1) // per_page + 1)
        self.icon_bytes: bytes | None = None
        self.bot_avatar_bytes: bytes | None = None
        self.title_emoji_bytes: bytes | None = None
        self.message: discord.Message | None = None

    async def fetch_assets(self):
        """Fetch the bot avatar (used as the blurred background), the
        command user's avatar (footer circle), and optional title emoji."""
        async with aiohttp.ClientSession() as s:
            bot_av_task    = _fetch_image_bytes(self.bot.user.display_avatar.url, s)
            user_av_task   = _fetch_image_bytes(self.ctx.author.display_avatar.url, s)
            emoji_url      = _emoji_to_url(self.emoji_str) if self.emoji_str else None
            emoji_task     = _fetch_image_bytes(emoji_url, s) if emoji_url else None

            results = await asyncio.gather(
                bot_av_task, user_av_task,
                emoji_task if emoji_task else asyncio.sleep(0, result=None),
            )
            self.icon_bytes        = results[0]   # bot avatar → blurred bg
            self.bot_avatar_bytes  = results[1]   # user avatar → footer circle
            self.title_emoji_bytes = results[2]

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
        return f"#{rank} · {count:,} reactions"

    async def render_file(self) -> discord.File:
        start = self.page * self.per_page
        chunk = self.rows[start:start + self.per_page]
        named = [(rank, self._resolve_name(uid), count)
                 for rank, (uid, count) in enumerate(chunk, start=start + 1)]
        page_str = f"page {self.page + 1}/{self.total}  ·  {len(self.rows)} ranked"
        buf = await asyncio.to_thread(
            _render_leaderboard_card,
            self.icon_bytes,
            self.title,
            self.subtitle,
            named,
            page_str,
            self.bot_avatar_bytes,            # really the *user* avatar now
            self.ctx.author.display_name,      # footer top line
            self._user_rank_text(),            # footer bottom line
            self.title_emoji_bytes,
        )
        return discord.File(fp=buf, filename="leaderboard.png")

    async def _refresh(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            file = await self.render_file()
            await interaction.message.edit(attachments=[file], view=self)
        except Exception as e:
            log.warning(f"leaderboard refresh failed: {e}")

    @discord.ui.button(label="◀", style=discord.ButtonStyle.grey)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            await self._refresh(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.grey)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.total - 1:
            self.page += 1
            await self._refresh(interaction)
        else:
            await interaction.response.defer()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.author.id

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class RCPageView(discord.ui.View):
    def __init__(self, rows, title, color, per_page=10, *,
                 author_id: int | None = None, header_subtitle: str = "",
                 footer_icon: str | None = None, guild: discord.Guild | None = None):
        super().__init__(timeout=60)
        self.rows = rows
        self.title = title
        self.color = color
        self.per_page = per_page
        self.page = 0
        self.total = max(1, (len(rows) - 1) // per_page + 1)
        self.author_id = author_id
        self.header_subtitle = header_subtitle
        self.footer_icon = footer_icon
        self.guild = guild

    def _resolve_name(self, user_id: int) -> str:
        if self.guild:
            m = self.guild.get_member(user_id)
            if m:
                return m.display_name
        return f"user-{user_id}"

    def build_embed(self):
        start = self.page * self.per_page
        chunk = self.rows[start:start + self.per_page]

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        # Figure out column widths from the FULL list so pagination doesn't
        # cause widths to jump between pages.
        max_count = max((c for _, c in self.rows), default=0)
        count_w = len(f"{max_count:,}")
        name_w = 18  # truncate longer names with an ellipsis

        lines = []
        for rank, (user_id, count) in enumerate(chunk, start=start + 1):
            name = self._resolve_name(user_id)
            if len(name) > name_w:
                name = name[: name_w - 1] + "…"
            # 3-char prefix slot — medal+space for top 3, " N." for the rest
            prefix = f"{medals[rank]} " if rank in medals else f"{rank:>2}."
            lines.append(f"{prefix} {name:<{name_w}} {count:>{count_w},}")

        body = "```\n" + "\n".join(lines) + "\n```" if lines else "*no data*"

        # show the caller their own rank if they're not in the visible page
        you_line = ""
        if self.author_id is not None:
            you_rank = next(
                (i + 1 for i, (uid, _) in enumerate(self.rows) if uid == self.author_id),
                None,
            )
            if you_rank is not None and not (start < you_rank <= start + len(chunk)):
                you_count = self.rows[you_rank - 1][1]
                you_line = f"\n-# you · `#{you_rank}` · `{you_count:,}` reactions"

        embed = discord.Embed(
            title=self.title,
            description=(
                (f"-# {self.header_subtitle}\n" if self.header_subtitle else "")
                + body
                + you_line
            ),
            color=self.color,
        )
        if self.footer_icon:
            embed.set_footer(
                text=f"page {self.page + 1}/{self.total} · {len(self.rows)} ranked",
                icon_url=self.footer_icon,
            )
        else:
            embed.set_footer(
                text=f"page {self.page + 1}/{self.total} · {len(self.rows)} ranked",
            )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.author_id is not None:
            return interaction.user.id == self.author_id
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.grey)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.grey)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.total - 1:
            self.page += 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

class ReactionsCog(commands.Cog, name="Reactions"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot or not reaction.message.guild:
            return
        _rc_upsert(reaction.message.guild.id, user.id, _emoji_str(reaction.emoji), 1)

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction, user):
        if user.bot or not reaction.message.guild:
            return
        _rc_upsert(reaction.message.guild.id, user.id, _emoji_str(reaction.emoji), -1)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return
        member = payload.member or self.bot.get_user(payload.user_id)
        if getattr(member, 'bot', False):
            return
        _rc_upsert(payload.guild_id, payload.user_id, _emoji_str(payload.emoji), 1)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return
        user = self.bot.get_user(payload.user_id)
        if getattr(user, 'bot', False):
            return
        _rc_upsert(payload.guild_id, payload.user_id, _emoji_str(payload.emoji), -1)

    @help_meta(
        usage="`.rc [@user] [emoji]`",
        desc="Shows reaction count for a user, optionally filtered by emoji.",
        examples=[".rc", ".rc @user", ".rc @user 😂"],
        params=[
            {"name": "user", "type": "discord.Member", "required": False, "desc": "The member to check. Defaults to yourself."},
            {"name": "emoji", "type": "str", "required": False, "desc": "Optional emoji filter."},
        ],
        note="Shows total reaction count, server rank, and per-emoji breakdown.",
    )
    @commands.command(name="rc", aliases=["reactioncount"])
    @commands.cooldown(2, 6, commands.BucketType.user)
    async def reaction_count(self, ctx, member: discord.Member = None, emoji: str = None):
        member = member or ctx.author
        conn = _get_react_conn()
        cx = conn.cursor()

        # Always pull the server-wide totals to compute rank.
        cx.execute("""
            SELECT user_id, SUM(count) as total FROM reaction_stats
            WHERE guild_id = ? GROUP BY user_id ORDER BY total DESC
        """, (ctx.guild.id,))
        rows = cx.fetchall()

        # Per-emoji or all-emoji total for THIS user
        if emoji:
            cx.execute("""
                SELECT SUM(count) FROM reaction_stats
                WHERE guild_id = ? AND user_id = ? AND emoji = ?
            """, (ctx.guild.id, member.id, emoji.strip()))
        else:
            cx.execute("""
                SELECT SUM(count) FROM reaction_stats
                WHERE guild_id = ? AND user_id = ?
            """, (ctx.guild.id, member.id))
        result = cx.fetchone()

        total_for_member = result[0] or 0
        # rank by overall reaction count (independent of per-emoji filter)
        rank = next(
            (i + 1 for i, (uid, _) in enumerate(rows) if uid == member.id), None
        )
        ranked_count = len(rows)

        embed = discord.Embed(color=get_embed_color(ctx.guild.id))
        embed.set_author(
            name=f"{member.display_name} · reaction stats",
            icon_url=member.display_avatar.url,
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        if emoji:
            embed.add_field(name=f"{emoji} used", value=f"**{total_for_member:,}**", inline=True)
        else:
            embed.add_field(name="total reactions", value=f"**{total_for_member:,}**", inline=True)

        if rank is not None:
            embed.add_field(
                name="server rank",
                value=f"**#{rank}** of {ranked_count}",
                inline=True,
            )
        else:
            embed.add_field(name="server rank", value="*unranked*", inline=True)

        embed.set_footer(
            text=f"{ctx.guild.name}",
            icon_url=ctx.guild.icon.url if ctx.guild.icon else self.bot.user.display_avatar.url,
        )
        await ctx.send(embed=embed)

    @help_meta(
        usage="`.rtop`",
        desc="Shows the reaction leaderboard for the whole server.",
        examples=[".rtop"],
        params=[],
        note="Displays the top users ranked by total reactions received.",
    )
    @commands.command(name="rtop", aliases=["reactiontop"])
    @commands.cooldown(1, 10, commands.BucketType.channel)
    async def reaction_top(self, ctx):
        conn = _get_react_conn()
        cx = conn.cursor()
        cx.execute("""
            SELECT user_id, SUM(count) as total FROM reaction_stats
            WHERE guild_id = ? GROUP BY user_id ORDER BY total DESC
        """, (ctx.guild.id,))
        rows = cx.fetchall()
        if not rows:
            return await ctx.send("no reaction data yet")
        view = RCImageView(
            self.bot, ctx, rows,
            title="Reaction Leaderboard",
            subtitle=f"top reacted users in /{ctx.guild.name}",
            accent=get_embed_color(ctx.guild.id),
        )
        await view.fetch_assets()
        file = await view.render_file()
        view.message = await ctx.send(file=file, view=view)

    @help_meta(
        usage="`.rctop <emoji>`",
        desc="Shows the leaderboard filtered to one specific emoji.",
        examples=[".rctop 😂", ".rctop :sob:", ".rctop fire"],
        params=[
            {"name": "emoji", "type": "str", "required": True, "desc": "The emoji to filter by (actual emoji, emoji name, or `:name:` format)."},
        ],
        note="Supports emoji names like `sob`, `joy`, `fire`, etc.",
    )
    @commands.command(name="rctop", aliases=["reactiontopemoji"])
    @commands.cooldown(1, 10, commands.BucketType.channel)
    async def reaction_top_emoji(self, ctx, emoji: str = None):
        if not emoji:
            return await ctx.send("give me an emoji. `.rctop 😄` or `.rctop sob` or `.rctop :sob:`")

        emoji = emoji.strip()
        emoji_name = None
        if emoji.startswith(':') and emoji.endswith(':'):
            emoji_name = emoji[1:-1]
        elif len(emoji) <= 20 and (emoji.isalnum() or '_' in emoji):
            emoji_name = emoji

        if emoji_name:
            emoji_map = {
                'sob': '😭', 'joy': '😂', 'heart': '❤️', 'thumbsup': '👍', 'thumbs_up': '👍',
                'thumbsdown': '👎', 'thumbs_down': '👎', 'fire': '🔥', '100': '💯', 'ok': '👌',
                'clap': '👏', 'pray': '🙏', 'muscle': '💪', 'eyes': '👀',
                'thinking': '🤔', 'shrug': '🤷', 'facepalm': '🤦', 'sweat': '😅', 'cry': '😢',
                'laugh': '😆', 'smile': '😊', 'grin': '😁', 'wink': '😉', 'blush': '😊',
                'heart_eyes': '😍', 'kiss': '😘', 'tongue': '😛', 'angry': '😠', 'rage': '😡',
                'confused': '😕', 'worried': '😟', 'sad': '😢', 'happy': '😄', 'excited': '🤩',
                'cool': '😎', 'nerd': '🤓', 'sunglasses': '😎', 'sleepy': '😴', 'tired': '😫',
                'sick': '🤢', 'vomit': '🤮', 'poop': '💩', 'shit': '💩',
                'middle_finger': '🖕', 'fuck_you': '🖕', 'peace': '✌️', 'v': '✌️', 'wave': '👋',
                'point_right': '👉', 'point_left': '👈', 'point_up': '👆', 'point_down': '👇',
                'raised_hands': '🙌', 'fist': '✊', 'punch': '👊', 'open_hands': '👐'
            }
            if emoji_name.lower() in emoji_map:
                emoji = emoji_map[emoji_name.lower()]
            else:
                return await ctx.send(f"unknown emoji name `{emoji_name}`. try the actual emoji or a common name like `sob`, `joy`, `heart`.")

        conn = _get_react_conn()
        cx = conn.cursor()
        cx.execute("""
            SELECT user_id, count FROM reaction_stats
            WHERE guild_id = ? AND emoji = ? ORDER BY count DESC
        """, (ctx.guild.id, emoji))
        rows = cx.fetchall()
        if not rows:
            return await ctx.send(f"no data for emoji `{emoji}` yet")
        view = RCImageView(
            self.bot, ctx, rows,
            title="Reaction Leaderboard",
            subtitle=f"top users of this emoji in /{ctx.guild.name}",
            accent=get_embed_color(ctx.guild.id),
            emoji_str=emoji,
        )
        await view.fetch_assets()
        file = await view.render_file()
        view.message = await ctx.send(file=file, view=view)

    @help_meta(
        usage="`.rur @user`",
        desc="Resets all reaction data for a specific user.",
        owner=True,
        examples=[".rur @user"],
        params=[
            {"name": "user", "type": "discord.Member", "required": True, "desc": "The member whose reaction data to wipe."},
        ],
        note="Owner only. This cannot be undone.",
    )
    @commands.command(name="rur", aliases=["resetusereaction"])
    async def reset_user_reactions(self, ctx, member: discord.Member):
        if not is_owner_or_creator(ctx):
            return await ctx.send("owner only")
        conn = _get_react_conn()
        conn.execute("DELETE FROM reaction_stats WHERE guild_id = ? AND user_id = ?",
                     (ctx.guild.id, member.id))
        conn.commit()
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    @help_meta(
        usage="`.rsr`",
        desc="Resets ALL reaction data for this server.",
        owner=True,
        examples=[".rsr"],
        params=[],
        note="Owner only. Requires confirmation via `yes` before executing.",
    )
    @commands.command(name="rsr", aliases=["resetservereactions"])
    async def reset_server_reactions(self, ctx):
        if not is_owner_or_creator(ctx):
            return await ctx.send("owner only")
        confirm_msg = await ctx.send("⚠️ wipe ALL reaction data for this server? type `yes` to confirm")
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ["yes", "no"]
        try:
            reply = await self.bot.wait_for("message", timeout=15.0, check=check)
        except asyncio.TimeoutError:
            return await confirm_msg.edit(content="timed out, cancelled")
        if reply.content.lower() != "yes":
            return await confirm_msg.edit(content="cancelled")
        conn = _get_react_conn()
        conn.execute("DELETE FROM reaction_stats WHERE guild_id = ?", (ctx.guild.id,))
        conn.commit()
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionsCog(bot))
