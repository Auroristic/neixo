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


def _render_edit_embed(snap: dict, guild_id: int, n: int) -> discord.Embed:
    author = snap["author"]
    embed = discord.Embed(
        description=snap["content"] or "*no text*",
        color=get_embed_color(guild_id),
        timestamp=datetime.fromtimestamp(snap["edited_at"], tz=timezone.utc),
    )
    embed.set_author(name=author.display_name, icon_url=snap["avatar"])
    embed.set_footer(
        text=f"edit #{n} · edited {int(time.time() - snap['edited_at'])}s ago"
    )
    embed.add_field(
        name="message",
        value=f"[jump]({snap['jump_url']})",
        inline=False,
    )
    _add_attachments(embed, snap["attachments"], snap["sticker"], "attachment")
    return embed


def _reaction_emoji_str(emoji) -> str:
    """Render a reaction emoji as a display string (unicode passthrough, custom → <:name:id>)."""
    if isinstance(emoji, str):
        return emoji
    if emoji.animated:
        return f"<a:{emoji.name}:{emoji.id}>"
    return f"<:{emoji.name}:{emoji.id}>"


def _render_reaction_embed(snap: dict, guild_id: int, n: int) -> discord.Embed:
    embed = discord.Embed(
        description=(
            f"removed {snap['emoji']} on **{snap['message_author'].display_name}**'s "
            f"[message]({snap['message_jump_url']})"
        ),
        color=get_embed_color(guild_id),
        timestamp=datetime.fromtimestamp(snap["removed_at"], tz=timezone.utc),
    )
    embed.set_author(name=snap["reactor"].display_name, icon_url=snap["reactor_avatar"])
    embed.set_footer(
        text=f"rsnipe #{n} · removed {int(time.time() - snap['removed_at'])}s ago"
    )
    return embed


class Snipe(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._deleted: dict[tuple[int, int], deque] = {}
        self._edited: dict[tuple[int, int], deque] = {}
        self._reactions: dict[tuple[int, int], deque] = {}

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

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.guild is None or after.author.bot:
            return
        if before.content == after.content:
            return
        key = (after.guild.id, after.channel.id)
        snap = {
            "content": before.content,
            "author": before.author,
            "avatar": before.author.display_avatar.url,
            "attachments": [a.url for a in before.attachments],
            "sticker": before.stickers[0].url if before.stickers else None,
            "edited_at": time.time(),
            "jump_url": before.jump_url,
        }
        dq = self._edited.setdefault(key, deque(maxlen=_SNIPE_KEEP))
        dq.appendleft(snap)

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.User):
        message = reaction.message
        if message.guild is None or user.bot:
            return
        key = (message.guild.id, message.channel.id)
        snap = {
            "emoji": _reaction_emoji_str(reaction.emoji),
            "reactor": user,
            "reactor_avatar": user.display_avatar.url,
            "message_author": message.author,
            "message_jump_url": message.jump_url,
            "removed_at": time.time(),
        }
        dq = self._reactions.setdefault(key, deque(maxlen=_SNIPE_KEEP))
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

    @commands.command(name="esnipe", aliases=["es"])
    @help_meta(
        usage="`.esnipe [n]`",
        desc="Shows the last edited message's pre-edit content in this channel (or the nth one).",
        section="Fun",
        examples=[".esnipe", ".es 2"],
        params=[
            {
                "name": "n",
                "type": "int",
                "required": False,
                "desc": "Which edit to show, 1 = most recent.",
            },
        ],
        note="only the pre-edit content is shown — the after-content is still in chat.",
    )
    async def esnipe(self, ctx: commands.Context, n: int = 1):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if n < 1:
            return await ctx.send("-# `n` has to be at least 1")
        key = (ctx.guild.id, ctx.channel.id)
        dq = self._edited.get(key)
        if not dq or n > len(dq):
            return await ctx.send("-# nothing edited here. yet.")
        snap = dq[n - 1]
        await ctx.send(embed=_render_edit_embed(snap, ctx.guild.id, n))

    @commands.command(name="rsnipe", aliases=["rs"])
    @help_meta(
        usage="`.rsnipe [n]`",
        desc="Shows the last removed reaction in this channel (or the nth one).",
        section="Fun",
        examples=[".rsnipe", ".rs 2"],
        params=[
            {
                "name": "n",
                "type": "int",
                "required": False,
                "desc": "Which removed reaction to show, 1 = most recent.",
            },
        ],
        note="only works while the reaction removal is still in my memory (up to 50 per channel).",
    )
    async def rsnipe(self, ctx: commands.Context, n: int = 1):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if n < 1:
            return await ctx.send("-# `n` has to be at least 1")
        key = (ctx.guild.id, ctx.channel.id)
        dq = self._reactions.get(key)
        if not dq or n > len(dq):
            return await ctx.send("-# no reactions removed here. yet.")
        snap = dq[n - 1]
        await ctx.send(embed=_render_reaction_embed(snap, ctx.guild.id, n))


async def setup(bot: commands.Bot):
    await bot.add_cog(Snipe(bot))
