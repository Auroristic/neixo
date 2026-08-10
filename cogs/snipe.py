"""
cogs/snipe.py  —  last deleted message sniping
"""

import logging
import time
from collections import deque
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils import get_embed_color, help_meta

log = logging.getLogger(__name__)

COG_META = {
    "category": "fun",
    "label": "Fun",
    "desc": "Snipe deleted messages.",
}

_SNIPE_KEEP = 5  # per-channel history


class Snipe(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # (guild_id, channel_id) -> deque of deleted-message snapshots
        self._deleted: dict[tuple[int, int], deque] = {}

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        key = (message.guild.id, message.channel.id)
        snap = {
            "content": message.content,
            "author": message.author,
            "avatar": message.author.display_avatar.url,
            "attachments": [a.url for a in message.attachments],
            "sticker": message.stickers[0].url if message.stickers else None,
            "deleted_at": time.time(),
            "reference": message.reference.resolved.content if (
                message.reference and message.reference.resolved
            ) else None,
        }
        dq = self._deleted.setdefault(key, deque(maxlen=_SNIPE_KEEP))
        dq.appendleft(snap)

    @commands.command(name="snipe")
    @help_meta(
        usage="`.snipe [n]`",
        desc="Shows the last deleted message in this channel (or the nth one).",
        section="Fun",
        examples=[".snipe", ".snipe 2"],
        params=[
            {
                "name": "n",
                "type": "int",
                "required": False,
                "desc": "Which deleted message to show, 1 = most recent.",
            },
        ],
        note="only works while the message is still in my memory (up to 5 per channel).",
    )
    async def snipe(self, ctx: commands.Context, n: int = 1):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if n < 1:
            return await ctx.send("-# `n` has to be at least 1")
        key = (ctx.guild.id, ctx.channel.id)
        dq = self._deleted.get(key)
        if not dq or n > len(dq):
            return await ctx.send("-# nothing deleted here. yet.")
        snap = dq[n - 1]
        author = snap["author"]
        embed = discord.Embed(
            description=snap["content"] or "*no text*",
            color=get_embed_color(ctx.guild.id),
            timestamp=datetime.fromtimestamp(snap["deleted_at"], tz=timezone.utc),
        )
        embed.set_author(name=author.display_name, icon_url=snap["avatar"])
        embed.set_footer(
            text=f"snipe #{n} · deleted {int(time.time() - snap['deleted_at'])}s ago"
        )
        if snap["reference"]:
            embed.add_field(name="replying to", value=snap["reference"][:150], inline=False)
        if snap["sticker"]:
            embed.set_image(url=snap["sticker"])
        elif snap["attachments"]:
            embed.set_image(url=snap["attachments"][0])
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Snipe(bot))
