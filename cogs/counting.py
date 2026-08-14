"""
cogs/counting.py  —  counting channel game
"""

import logging

import discord
from discord.ext import commands

from utils import DATA_DIR, help_meta, is_owner_or_creator, load_json, save_json

log = logging.getLogger(__name__)

COUNTING_FILE = f"{DATA_DIR}/counting.json"

COG_META = {
    "category": "fun",
    "label": "Fun",
    "desc": "Counting channel game.",
}


class Counting(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # guild_id -> {"channel": id, "current": int, "last_user": id}
        self._state: dict[str, dict] = {}

    async def cog_load(self):
        self._state = load_json(COUNTING_FILE) or {}
        for g in self._state.values():
            g.setdefault("scores", {})

    def _save(self):
        save_json(COUNTING_FILE, self._state)

    @commands.group(name="counting", invoke_without_command=True)
    @help_meta(
        usage="`.counting <#channel>`  ·  `.counting off`",
        desc="turns a channel into a counting game: numbers must go up by 1",
        section="Fun",
        examples=[".counting #counting", ".counting off", ".counting"],
        params=[
            {
                "name": "channel",
                "type": "discord.TextChannel",
                "required": False,
                "desc": "Channel to use as the counting channel, or `off` to disable.",
            },
        ],
        note="wrong number or counting twice in a row resets the count to 0.",
    )
    async def counting(self, ctx: commands.Context, channel: discord.TextChannel = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        gid = str(ctx.guild.id)
        if channel is None:
            cur = self._state.get(gid)
            if not cur:
                return await ctx.send("-# no counting channel set. `.counting #channel` to start.")
            ch = ctx.guild.get_channel(int(cur["channel"]))
            return await ctx.send(
                f"-# counting is on in {ch.mention if ch else cur['channel']} — current count: **{cur['current']}**"
            )
        if not is_owner_or_creator(ctx) and not getattr(getattr(ctx.author, "guild_permissions", None), "administrator", False):
            return await ctx.send("-# admin only")
        if channel.id == ctx.channel.id:
            return await ctx.send("-# can't use this channel, it'd be chaos")

        scores = self._state.get(gid, {}).get("scores", {})
        self._state[gid] = {"channel": str(channel.id), "current": 0, "last_user": None, "scores": scores}
        self._save()
        await ctx.send(f"-# counting game on in {channel.mention}. start at **1**.")

    @counting.command(name="off")
    @help_meta(
        usage="`.counting off`",
        desc="turns off the counting game in the server",
        section="Fun",
        examples=[".counting off"],
        params=[],
        note="admin only.",
    )
    async def counting_cmd_off(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if not is_owner_or_creator(ctx) and not getattr(getattr(ctx.author, "guild_permissions", None), "administrator", False):
            return await ctx.send("-# admin only")
        gid = str(ctx.guild.id)
        if gid in self._state:
            del self._state[gid]
            self._save()
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    @commands.command(name="top")
    @help_meta(
        usage="`.top`",
        desc="Shows the top counters from the counting channel.",
        section="Fun",
        examples=[".top"],
        params=[],
        note="counts correct numbers only.",
    )
    async def counting_top(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        gid = str(ctx.guild.id)
        conf = self._state.get(gid)
        if not conf:
            return await ctx.send("-# no counting channel set.")
        scores = conf.get("scores", {})
        if not scores:
            return await ctx.send("-# nobody has counted correctly yet. start at **1**.")
        rows = sorted(((int(uid), n) for uid, n in scores.items()), key=lambda x: -x[1])
        from cogs.serverstats import LBPageView
        view = LBPageView(
            self.bot, ctx, rows,
            title="Counting Leaderboard",
            subtitle=f"most correct counts in /{ctx.guild.name}",
            unit=" counts",
        )
        await view.fetch_assets()
        file = await view.render_file()
        view.message = await ctx.send(file=file, view=view)

    @commands.command(name="countingoff", hidden=True)
    @help_meta(
        usage="`.countingoff`",
        desc="Turns the counting game off (creator only).",
        section="Fun",
        examples=[".countingoff"],
        params=[],
        note="creator only. alternative to `.counting off`.",
    )
    @commands.check(lambda ctx: is_owner_or_creator(ctx))
    async def counting_off(self, ctx):
        gid = str(ctx.guild.id)
        if gid in self._state:
            del self._state[gid]
            self._save()
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        gid = str(message.guild.id)
        conf = self._state.get(gid)
        if not conf or str(message.channel.id) != str(conf["channel"]):
            return
        content = (message.content or "").strip()
        if not content.isdigit():
            return

        n = int(content)
        if n == conf["current"] + 1 and conf.get("last_user") != message.author.id:
            conf["current"] = n
            conf["last_user"] = message.author.id
            scores = conf.setdefault("scores", {})
            scores[str(message.author.id)] = scores.get(str(message.author.id), 0) + 1
            self._save()
            try:
                await message.add_reaction("<:pinklotus:1263556545686405170>")
            except discord.HTTPException:
                pass
        else:
            conf["current"] = 0
            conf["last_user"] = None
            self._save()
            try:
                await message.add_reaction("<:redlotus:1263556248310386800>")
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Counting(bot))
