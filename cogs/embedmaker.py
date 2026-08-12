"""
cogs/embedmaker.py  —  staff embed builder
"""

import logging
import re

import discord
from discord.ext import commands

from utils import help_meta, is_owner_or_creator

log = logging.getLogger(__name__)

COG_META = {
    "category": "general",
    "label": "General",
    "desc": "Staff embed builder.",
}


class EmbedMaker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _staff(self, ctx) -> bool:
        if ctx.guild is None:
            return False
        if is_owner_or_creator(ctx):
            return True
        perms = getattr(ctx.author, "guild_permissions", None)
        return bool(perms and perms.administrator)

    @commands.command(name="embed")
    @help_meta(
        usage="`.embed <title> | <description>`",
        desc="Posts a clean embed. Split title and description with ` | `.",
        section="General",
        examples=[".embed Announcement | we're having a movie night friday"],
        params=[
            {"name": "content", "type": "str", "required": True, "desc": "`title | description` — optional `--color #hex` at the end."},
        ],
        note="Staff only.",
    )
    async def embed(self, ctx: commands.Context, *, content: str = None):
        if not await self._staff(ctx):
            return await ctx.send("-# staff only")
        if not content:
            return await ctx.send("-# usage: `.embed <title> | <description>` — optional `--color #hex`")

        color = 0x121516
        m = re.search(r"--color\s+([0-9a-fA-F]{6})$", content)
        if m:
            color = int(m.group(1), 16)
            content = content[: m.start()].rstrip()

        parts = content.split("|", 1)
        title = parts[0].strip()
        desc = parts[1].strip() if len(parts) > 1 else None
        if not title:
            return await ctx.send("-# need a title before the ` | `")

        embed = discord.Embed(title=title, color=color)
        if desc:
            embed.description = desc
        embed.set_footer(text=f"posted by {ctx.author.display_name}")
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(EmbedMaker(bot))
