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
    "category": "theme",
    "label": "Theme",
    "desc": "Custom embed generation and server theme layouts.",
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
        usage="`.embed <title> | <description> [--color #hex]`",
        desc="Posts a sleek custom embed in the current channel. Separates title and description with ` | `.",
        section="Theme",
        perm_tier="admin",
        discord_perms=["manage_messages"],
        examples=[
            ".embed Announcement | Movie night this Friday at 8 PM UTC",
            ".embed Server Rules | 1. Be respectful\n2. No spam --color #707080",
        ],
        params=[
            {"name": "content", "type": "str", "required": True, "desc": "`<title> | <description>` format with optional `--color #hex` flag at the end."},
        ],
        note="Requires Administrator or Manage Messages permission. Automatically deletes the invoking command.",
    )
    async def embed(self, ctx: commands.Context, *, content: str = None):
        if not await self._staff(ctx):
            return await ctx.send("-# staff only")
        if not content:
            return await ctx.send("-# usage: `.embed <title> | <description>` — optional `--color #hex`")

        color = 0x121516
        m = re.search(r"--color\s+#?([0-9a-fA-F]{6})$", content)
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
            await ctx.send(embed=embed)
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
        except discord.HTTPException as e:
            await ctx.send(f"-# couldn't send embed: {str(e).lower()}")


async def setup(bot: commands.Bot):
    await bot.add_cog(EmbedMaker(bot))
