"""
cogs/milestones.py  —  member count milestone cards
"""

import asyncio
import io
import logging

import aiohttp
import discord
from discord.ext import commands

from utils import DATA_DIR, help_meta, is_owner_or_creator, load_json, save_json

log = logging.getLogger(__name__)

MILESTONES_FILE = f"{DATA_DIR}/milestones.json"

# milestone targets to celebrate
MILESTONES = [50, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000, 2500, 3000, 4000, 5000]

COG_META = {
    "category": "admin",
    "label": "Admin",
    "desc": "Member milestone celebration cards and announcements.",
}


def _render_milestone_card(
    icon_bytes: bytes | None,
    guild_name: str,
    count: int,
) -> io.BytesIO:
    from PIL import Image, ImageDraw
    from cogs.serverstats import _load_font, _make_glass_backdrop

    W, H = 900, 500
    bg = _make_glass_backdrop(icon_bytes, W, H, dark_tint=0.55, blur_radius=20)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad = 35
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=28, fill=(0, 0, 0, 95))
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=28, outline=(255, 255, 255, 55), width=1)
    cd.line([(pad + 25, pad + 1), (W - pad - 25, pad + 1)], fill=(255, 255, 255, 95), width=1)
    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    title_font = _load_font(64, bold=True)
    sub_font = _load_font(26, bold=False)

    title_font.draw(draw, (W // 2, 165), f"{count:,}", fill=(255, 255, 255, 255), anchor="mm")
    sub_font.draw(draw, (W // 2, 245), "members!", fill=(225, 230, 240, 220), anchor="mm")
    sub_font.draw(draw, (W // 2, 315), guild_name[:45], fill=(160, 165, 175, 180), anchor="mm")

    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG", quality=92)
    buf.seek(0)
    return buf


class Milestones(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _admin(self, ctx) -> bool:
        if ctx.guild is None:
            return False
        if is_owner_or_creator(ctx):
            return True
        perms = getattr(ctx.author, "guild_permissions", None)
        return bool(perms and perms.administrator)

    @commands.group(name="milestone", aliases=["milestones"], invoke_without_command=True)
    @help_meta(
        usage="`.milestone <#channel>`  ·  `.milestone off`  ·  `.milestone status`",
        desc="Automatically posts a celebratory dark milestone card when the server reaches member goals (100, 250, 500...).",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".milestone #announcements", ".milestone status", ".milestone off"],
        params=[{"name": "channel", "type": "channel", "required": False, "desc": "Channel to send milestone cards to (enables milestones)."}],
        note="Requires Administrator or Manage Server permission.",
    )
    async def milestone(self, ctx: commands.Context, channel: discord.TextChannel = None):
        if channel is not None:
            return await self.milestone_set(ctx, channel)
        await ctx.send("-# milestone commands: `.milestone <#channel>` · `.milestone off` · `.milestone status`")

    @milestone.command(name="off")
    @help_meta(
        usage="`.milestone off`",
        desc="Disables milestone celebration cards in the server.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".milestone off"],
        params=[],
        note="Requires Administrator permission.",
    )
    async def milestone_off(self, ctx: commands.Context):
        if not await self._admin(ctx):
            return await ctx.send("-# admin only")
        state = load_json(MILESTONES_FILE) or {}
        if state.pop(str(ctx.guild.id), None):
            save_json(MILESTONES_FILE, state)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    @milestone.command(name="status")
    @help_meta(
        usage="`.milestone status`",
        desc="Shows whether milestone celebration cards are enabled and which channel they post to.",
        section="Server Management",
        perm_tier="public",
        examples=[".milestone status"],
        params=[],
        note="Available to all members.",
    )
    async def milestone_status(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        conf = (load_json(MILESTONES_FILE) or {}).get(str(ctx.guild.id))
        if not conf:
            return await ctx.send("-# milestone cards are off. `.milestone #channel` to turn on.")
        ch = ctx.guild.get_channel(int(conf["channel_id"]))
        await ctx.send(f"-# milestone cards on in {ch.mention if ch else conf['channel_id']}.")

    @milestone.command(name="set", aliases=["on"])
    @help_meta(
        usage="`.milestone set <#channel>`",
        desc="Turns on milestone celebration cards and sets the announcement channel.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".milestone set #announcements", ".milestone on #general"],
        params=[{"name": "channel", "type": "channel", "required": True, "desc": "Channel to post milestone cards to."}],
        note="Requires Administrator or Manage Server permission.",
    )
    async def milestone_set(self, ctx: commands.Context, channel: discord.TextChannel = None):
        if not await self._admin(ctx):
            return await ctx.send("-# admin only")
        if channel is None:
            return await ctx.send("-# usage: `.milestone #channel`")
        state = load_json(MILESTONES_FILE) or {}
        count = ctx.guild.member_count or len(ctx.guild.members)
        passed = [m for m in MILESTONES if m <= count]
        state[str(ctx.guild.id)] = {
            "channel_id": str(channel.id),
            "last": max(passed, default=0),
        }
        save_json(MILESTONES_FILE, state)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        conf = (load_json(MILESTONES_FILE) or {}).get(str(member.guild.id))
        if not conf:
            return
        channel = member.guild.get_channel(int(conf["channel_id"]))
        if channel is None:
            return
        count = member.guild.member_count or len(member.guild.members)
        last = conf.get("last", 0)
        hit = None
        for m in MILESTONES:
            if count >= m and m > last:
                hit = m
        if hit is None:
            return
        conf["last"] = hit
        state = load_json(MILESTONES_FILE) or {}
        state[str(member.guild.id)] = conf
        save_json(MILESTONES_FILE, state)

        icon_bytes = None
        try:
            if member.guild.icon:
                async with aiohttp.ClientSession() as s:
                    async with s.get(member.guild.icon.url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                        if r.status == 200:
                            icon_bytes = await r.read()
        except Exception:
            pass
        buf = await asyncio.to_thread(_render_milestone_card, icon_bytes, member.guild.name, hit)
        try:
            await channel.send(
                f"we hit **{hit:,}** members!",
                file=discord.File(fp=buf, filename="milestone.png"),
            )
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Milestones(bot))
