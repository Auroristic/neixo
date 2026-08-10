"""
cogs/bumps.py  —  disboard bump detection + per-server bump leaderboard

Two signals for the same bump event:
  1. disboard's own "Bump done!" message (mentions the bumper)
  2. bleed bot's "<@user>, thank you for bumping!" ack (mentions the bumper)

Both fire within seconds of each other, so a per-channel cooldown window of
2 hours (disboard's bump cooldown) makes sure one bump is never counted twice.
"""

import asyncio
import logging
import os
import re
import sqlite3 as _sql
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils import DATA_DIR, get_embed_color, help_meta

log = logging.getLogger(__name__)

# disboard's bot id + the two signals we watch for
DISBOARD_ID = 302050872383242240
DISBOARD_SIGNAL = "bump done"
BLEED_SIGNAL = "thank you for bumping"

# disboard's bump cooldown is 2h — anything inside that window is the same
# bump, so counting both signals would double-count
BUMP_WINDOW_SECONDS = 7200

BUMPS_DB = f"{DATA_DIR}/bumps.db"
os.makedirs(DATA_DIR, exist_ok=True)

_bump_conn: _sql.Connection | None = None

_MENTION_RE = re.compile(r"<@!?(\d+)>")

COG_META = {
    "category": "general",
    "label": "Server Stats",
    "desc": "Disboard bump tracking & bump leaderboard.",
}


def _get_conn() -> _sql.Connection:
    global _bump_conn
    if _bump_conn is None:
        _bump_conn = _sql.connect(BUMPS_DB, check_same_thread=False)
        _bump_conn.execute("PRAGMA journal_mode=WAL")
        _bump_conn.execute("PRAGMA synchronous=NORMAL")
        _bump_conn.executescript("""
            CREATE TABLE IF NOT EXISTS bump_counts (
                guild_id  TEXT NOT NULL,
                user_id   TEXT NOT NULL,
                count     INTEGER DEFAULT 1,
                last_bump TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
    return _bump_conn


def _extract_bumper(content: str, mentions: list) -> int | None:
    """Return the bumper's user id from message mentions, with a raw
    content fallback for messages whose mentions didn't resolve."""
    for u in mentions:
        if not getattr(u, "bot", False):
            return u.id
    m = _MENTION_RE.search(content or "")
    if m:
        return int(m.group(1))
    return None


class Bumps(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        # per (guild_id, channel_id) -> last counted bump timestamp
        self._last_bump: dict[tuple[int, int], float] = {}

    async def cog_load(self):
        _get_conn()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return
        low = (message.content or "").lower()

        if message.author.id == DISBOARD_ID and DISBOARD_SIGNAL in low:
            bumper_id = _extract_bumper(message.content, message.mentions)
        elif message.author.bot and BLEED_SIGNAL in low:
            bumper_id = _extract_bumper(message.content, message.mentions)
        else:
            return

        if bumper_id is None:
            return

        key = (message.guild.id, message.channel.id)
        now = time.time()
        if now - self._last_bump.get(key, 0.0) < BUMP_WINDOW_SECONDS:
            return
        self._last_bump[key] = now

        async with self._lock:
            conn = _get_conn()
            conn.execute(
                "INSERT INTO bump_counts (guild_id, user_id, count, last_bump) "
                "VALUES (?, ?, 1, ?) "
                "ON CONFLICT(guild_id, user_id) DO UPDATE SET "
                "count = count + 1, last_bump = excluded.last_bump",
                (
                    str(message.guild.id),
                    str(bumper_id),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

        # silent confirmation, per house style
        try:
            await message.add_reaction("<:pinklotus:1263556545686405170>")
        except discord.HTTPException:
            pass

    def _top(self, gid: str, limit: int):
        return _get_conn().execute(
            "SELECT user_id, count FROM bump_counts "
            "WHERE guild_id = ? ORDER BY count DESC, last_bump ASC LIMIT ?",
            (gid, limit),
        ).fetchall()

    def _user_stats(self, gid: str, uid: int):
        conn = _get_conn()
        row = conn.execute(
            "SELECT count FROM bump_counts WHERE guild_id = ? AND user_id = ?",
            (gid, str(uid)),
        ).fetchone()
        if not row:
            return None, None
        rank = conn.execute(
            "SELECT COUNT(*) FROM bump_counts WHERE guild_id = ? AND count > ?",
            (gid, row[0]),
        ).fetchone()[0] + 1
        return row, rank

    @commands.command(name="bumps")
    @help_meta(
        usage="`.bumps [@user]`",
        desc="Shows the disboard bump leaderboard, or one user's bump count.",
        section="Leaderboards",
        examples=[".bumps", ".bumps @someone"],
        params=[
            {
                "name": "user",
                "type": "discord.Member",
                "required": False,
                "desc": "Show a specific user's bump count and rank.",
            },
        ],
        note="tracks disboard bumps. one bump every 2 hours, so it's a fair race.",
    )
    async def bumps(self, ctx: commands.Context, user: discord.Member = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        gid = str(ctx.guild.id)

        if user:
            row, rank = self._user_stats(gid, user.id)
            if not row:
                return await ctx.send(
                    f"-# {user.display_name} hasn't bumped yet. get to it."
                )
            embed = discord.Embed(
                title="bumps",
                description=(
                    f"**{user.display_name}** — {row[0]} bump"
                    f"{'s' if row[0] != 1 else ''} (rank #{rank})"
                ),
                color=get_embed_color(ctx.guild.id),
            )
            return await ctx.send(embed=embed)

        rows = self._top(gid, 200)
        if not rows:
            return await ctx.send(
                "-# no bumps tracked yet. run /bump on disboard and i'll count it."
            )

        # same PIL leaderboard card as .lb messages / .lb vctime
        from cogs.serverstats import LBPageView

        view = LBPageView(
            self.bot,
            ctx,
            rows,
            title="Bump Leaderboard",
            subtitle=f"most disboard bumps in /{ctx.guild.name}",
            unit=" bumps",
        )
        await view.fetch_assets()
        file = await view.render_file()
        view.message = await ctx.send(file=file, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Bumps(bot))
