"""
cogs/giveaways.py  —  reaction-based giveaways
"""

import asyncio
import logging
import random
import re
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from utils import DATA_DIR, get_embed_color, help_meta, is_owner_or_creator, load_json, save_json

log = logging.getLogger(__name__)

GIVEAWAYS_FILE = f"{DATA_DIR}/giveaways.json"
ENTRY_EMOJI = "\U0001f389"  # 🎉

COG_META = {
    "category": "fun",
    "label": "Fun",
    "desc": "Reaction-based giveaways.",
}


def _load_giveaways() -> dict:
    return load_json(GIVEAWAYS_FILE) or {}


def _save_giveaways(state: dict) -> None:
    save_json(GIVEAWAYS_FILE, state)


class Giveaways(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._tasks: dict[str, asyncio.Task] = {}

    async def cog_load(self):
        state = _load_giveaways()
        for gid, gs in state.items():
            for mid, g in gs.items():
                if g.get("ended"):
                    continue
                try:
                    end = datetime.fromisoformat(g["end_iso"])
                except Exception:
                    continue
                delay = (end - datetime.now(timezone.utc)).total_seconds()
                key = f"{gid}:{mid}"
                if delay <= 0:
                    self.bot.loop.create_task(self._finish(int(gid), int(mid)))
                else:
                    self._tasks[key] = asyncio.create_task(self._wait_and_finish(delay, int(gid), int(mid)))

    def cog_unload(self):
        for t in self._tasks.values():
            if not t.done():
                t.cancel()
        self._tasks.clear()

    async def _wait_and_finish(self, delay: float, guild_id: int, msg_id: int):
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await self._finish(guild_id, msg_id)

    async def _finish(self, guild_id: int, msg_id: int):
        key = f"{guild_id}:{msg_id}"
        self._tasks.pop(key, None)
        guild = self.bot.get_guild(guild_id)
        state = _load_giveaways()
        g = state.get(str(guild_id), {}).get(str(msg_id))
        if not g or g.get("ended"):
            return
        g["ended"] = True
        _save_giveaways(state)

        if guild is None:
            return
        channel = guild.get_channel(int(g["channel_id"]))
        if channel is None:
            return
        try:
            message = await channel.fetch_message(msg_id)
        except discord.HTTPException:
            return

        winner = None
        try:
            entry_reaction = next((r for r in message.reactions if str(r.emoji) == ENTRY_EMOJI), None)
            reactors = []
            if entry_reaction:
                reactors = [u for u in await entry_reaction.users().flatten() if not u.bot]
        except discord.HTTPException:
            reactors = []
        if reactors:
            winner = random.choice(reactors)

        embed = discord.Embed(
            title="giveaway ended",
            description=f"prize: **{g['prize']}**",
            color=get_embed_color(guild_id),
        )
        embed.set_footer(text=f"hosted by <@{g['host_id']}>")
        try:
            await message.edit(content=None, embed=embed, view=None)
        except discord.HTTPException:
            pass
        if winner:
            await channel.send(f"🎉 {winner.mention} won **{g['prize']}**! (giveaway by <@{g['host_id']}>)")
        else:
            await channel.send(f"no one entered for **{g['prize']}**. sad.")

    @commands.command(name="giveaway")
    @help_meta(
        usage="`.giveaway <duration> <prize>`",
        desc="Starts a giveaway. React 🎉 to enter, winner picked at random.",
        section="Fun",
        examples=[".giveaway 1h nitro", ".giveaway 2d discord mod role"],
        params=[
            {"name": "duration", "type": "str", "required": True, "desc": "How long it runs: `30m`, `2h`, `1d`, `1w`."},
            {"name": "prize", "type": "str", "required": True, "desc": "The prize."},
        ],
        note="Anyone can host. Winners are picked randomly from 🎉 reactors.",
    )
    async def giveaway(self, ctx: commands.Context, duration: str = None, *, prize: str = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if not duration or not prize:
            return await ctx.send("-# usage: `.giveaway <duration> <prize>` — e.g. `.giveaway 1h nitro`")
        secs = _parse_duration(duration)
        if not secs:
            return await ctx.send("-# couldn't parse that duration. try `30m`, `2h`, `1d`, `1w`")
        if secs < 60:
            return await ctx.send("-# giveaways need at least a minute")

        end = datetime.now(timezone.utc) + timedelta(seconds=secs)
        embed = discord.Embed(
            title="giveaway!",
            description=f"prize: **{prize.strip()[:100]}**\nreact {ENTRY_EMOJI} to enter",
            color=get_embed_color(ctx.guild.id),
        )
        embed.add_field(name="ends", value=f"<t:{int(end.timestamp())}:R>")
        embed.set_footer(text=f"hosted by {ctx.author.display_name}")
        try:
            msg = await ctx.send(embed=embed)
            await msg.add_reaction(ENTRY_EMOJI)
        except discord.HTTPException:
            return await ctx.send("-# couldn't start the giveaway (missing perms?)")

        state = _load_giveaways()
        state.setdefault(str(ctx.guild.id), {})[str(msg.id)] = {
            "channel_id": str(ctx.channel.id),
            "prize": prize.strip()[:100],
            "end_iso": end.isoformat(),
            "host_id": ctx.author.id,
            "ended": False,
        }
        _save_giveaways(state)
        key = f"{ctx.guild.id}:{msg.id}"
        self._tasks[key] = asyncio.create_task(self._wait_and_finish(secs, ctx.guild.id, msg.id))

    @commands.command(name="giveawaycancel")
    @help_meta(
        usage="`.giveawaycancel`",
        desc="Cancels the most recent active giveaway in this channel.",
        section="Fun",
        examples=[".giveawaycancel"],
        params=[],
        note="Admin only.",
    )
    async def giveaway_cancel(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        perms = getattr(ctx.author, "guild_permissions", None)
        if not (perms and perms.administrator) and not is_owner_or_creator(ctx):
            return await ctx.send("-# admin only")
        state = _load_giveaways()
        gs = state.get(str(ctx.guild.id), {})
        active = [
            (mid, g) for mid, g in gs.items()
            if not g.get("ended") and str(g.get("channel_id")) == str(ctx.channel.id)
        ]
        if not active:
            return await ctx.send("-# no active giveaway in this channel")
        mid, g = max(active, key=lambda x: x[1]["end_iso"])
        g["ended"] = True
        _save_giveaways(state)
        key = f"{ctx.guild.id}:{mid}"
        t = self._tasks.pop(key, None)
        if t and not t.done():
            t.cancel()
        try:
            msg = await ctx.channel.fetch_message(int(mid))
            embed = discord.Embed(
                title="giveaway cancelled",
                description=f"**{g['prize']}**",
                color=get_embed_color(ctx.guild.id),
            )
            await msg.edit(content=None, embed=embed, view=None)
        except discord.HTTPException:
            pass
        await ctx.send("-# giveaway cancelled")


def _parse_duration(s: str) -> int | None:
    m = re.match(r"^(\d+)([smhdw])$", s.strip().lower())
    if not m:
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return int(m.group(1)) * units[m.group(2)]


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaways(bot))
