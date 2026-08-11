"""
cogs/snipe.py  —  deleted / edited message and removed-reaction sniping
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
    "desc": "Snipe deleted/edited messages and removed reactions.",
}

_SNIPE_KEEP = 50  # per-channel history


def _add_attachments(embed: discord.Embed, images: list[str], sticker: str | None, label: str) -> None:
    """Set the embed image to the first attachment (or sticker), and add the rest as fields.

    When a sticker takes the image slot, ALL attachments go to fields (otherwise
    the first attachment would be silently dropped).
    """
    if sticker:
        embed.set_image(url=sticker)
        start = 0
    elif images:
        embed.set_image(url=images[0])
        start = 1
    else:
        return
    for i, url in enumerate(images[start:], start=start + 1):
        embed.add_field(name=f"{label} {i}", value=f"[attachment {i}]({url})", inline=False)


def _render_deleted_embed(snap: dict, guild_id: int, n: int) -> discord.Embed:
    author = snap["author"]
    embed = discord.Embed(
        description=snap["content"] or "*no text*",
        color=get_embed_color(guild_id),
        timestamp=datetime.fromtimestamp(snap["deleted_at"], tz=timezone.utc),
    )
    embed.set_author(name=author.display_name, icon_url=snap["avatar"])
    embed.set_footer(
        text=f"snipe #{n} · deleted {int(time.time() - snap['deleted_at'])}s ago"
    )
    if snap["reference"]:
        embed.add_field(name="replying to", value=snap["reference"][:150], inline=False)
    _add_attachments(embed, snap["attachments"], snap["sticker"], "attachment")
    return embed


class Snipe(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
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
            # resolved is a DeletedReferencedMessage (no .content) when the
            # replied-to message was itself deleted — getattr handles that.
            "reference": getattr(message.reference.resolved, "content", None) if message.reference else None,
        }
        dq = self._deleted.setdefault(key, deque(maxlen=_SNIPE_KEEP))
        dq.appendleft(snap)

    @commands.command(name="snipe", aliases=["s"])
    @help_meta(
        usage="`.snipe [n]`",
        desc="Shows the last deleted message in this channel (or the nth one).",
        section="Fun",
        examples=[".snipe", ".s 2"],
        params=[
            {
                "name": "n",
                "type": "int",
                "required": False,
                "desc": "Which deleted message to show, 1 = most recent.",
            },
        ],
        note="only works while the message is still in my memory (up to 50 per channel).",
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
        await ctx.send(embed=_render_deleted_embed(snap, ctx.guild.id, n))


async def setup(bot: commands.Bot):
    await bot.add_cog(Snipe(bot))
