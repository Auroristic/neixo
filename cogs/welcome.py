"""
cogs/welcome.py  —  image welcome cards on member join
"""

import asyncio
import io
import logging

import aiohttp
import discord
from discord.ext import commands

from utils import DATA_DIR, help_meta, is_owner_or_creator, load_json, save_json

log = logging.getLogger(__name__)

WELCOME_FILE = f"{DATA_DIR}/welcome.json"

COG_META = {
    "category": "admin",
    "label": "Admin",
    "desc": "Automated image welcome cards and greetings for new members.",
}


def _render_welcome_card(
    avatar_bytes: bytes | None,
    banner_bytes: bytes | None,
    guild_name: str,
    member_name: str,
    member_count: int,
) -> io.BytesIO:
    from PIL import Image, ImageDraw, ImageFilter
    from cogs.serverstats import _circle_avatar, _load_font

    W, H = 900, 500
    source_bytes = banner_bytes or avatar_bytes
    if source_bytes:
        try:
            src = Image.open(io.BytesIO(source_bytes)).convert("RGB")
            thumb = src.resize((180, 100), Image.Resampling.BILINEAR)
            blurred = thumb.filter(ImageFilter.GaussianBlur(10))
            bg = blurred.resize((W, H), Image.Resampling.BICUBIC)
        except Exception:
            bg = Image.new("RGB", (W, H), (14, 15, 18))
    else:
        bg = Image.new("RGB", (W, H), (14, 15, 18))

    overlay = Image.new("RGB", (W, H), (12, 13, 16))
    bg = Image.blend(bg, overlay, 0.72)

    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        alpha = int(75 * (y / H))
        gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
    bg = Image.alpha_composite(bg.convert("RGBA"), grad)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad = 35
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=28, fill=(18, 19, 24, 180))
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=28, outline=(210, 215, 230, 45), width=1)
    cd.line([(pad + 25, pad + 1), (W - pad - 25, pad + 1)], fill=(255, 255, 255, 65), width=1)
    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    title_font = _load_font(44, bold=True)
    sub_font = _load_font(24, bold=False)

    av_size = 140
    if avatar_bytes:
        try:
            av = _circle_avatar(avatar_bytes, av_size)
            av_x = (W - av_size) // 2
            av_y = 65
            bg.paste(av, (av_x, av_y), av)
            draw.ellipse([av_x, av_y, av_x + av_size, av_y + av_size], outline=(255, 255, 255, 50), width=1)
        except Exception:
            pass

    title_font.draw(draw, (W // 2, 235), "welcome", fill=(255, 255, 255, 255), anchor="mm")
    raw_name = str(member_name or "")
    name = raw_name if len(raw_name) <= 40 else raw_name[:39] + "\u2026"
    sub_font.draw(draw, (W // 2, 290), name, fill=(225, 230, 240, 220), anchor="mm")
    sub_font.draw(
        draw,
        (W // 2, 335),
        f"member #{member_count:,} of {guild_name}" if len(guild_name) <= 45 else f"member #{member_count:,}",
        fill=(160, 165, 175, 180),
        anchor="mm",
    )

    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG", quality=92)
    buf.seek(0)
    return buf


async def _fetch_member_art(member) -> tuple[bytes | None, bytes | None]:
    """Fetch a member's avatar and their guild's banner bytes (best-effort).

    The outer try only guards session creation; per-URL failures are
    tolerated inside _get so one bad URL never blanks the other.
    """
    avatar_bytes = banner_bytes = None
    urls = [member.display_avatar.url]
    if member.guild.banner:
        urls.append(member.guild.banner.url)
    try:
        async with aiohttp.ClientSession() as s:
            async def _get(url):
                try:
                    async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                        if r.status == 200:
                            return await r.read()
                except Exception:
                    pass
                return None
            results = await asyncio.gather(*[_get(u) for u in urls])
            avatar_bytes = results[0] if len(results) > 0 else None
            banner_bytes = results[1] if len(results) > 1 else None
    except Exception:
        pass
    return avatar_bytes, banner_bytes


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="welcome", invoke_without_command=True)
    @help_meta(
        usage="`.welcome setup <#channel> [message]`  ·  `.welcome off`  ·  `.welcome test`  ·  `.welcome status`",
        desc="Manages automated dark-themed welcome cards rendered when new members join.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".welcome setup #welcome hey {user} welcome to our server!", ".welcome test"],
        params=[],
        note="Requires Administrator or Manage Server permission. `{user}` in the custom message is replaced with member mention.",
    )
    async def welcome(self, ctx: commands.Context):
        await ctx.send(
            "-# welcome commands: `.welcome setup #channel [message]` · `.welcome off` "
            "· `.welcome test` · `.welcome status`"
        )

    async def _admin(self, ctx) -> bool:
        if ctx.guild is None:
            return False
        if is_owner_or_creator(ctx):
            return True
        perms = getattr(ctx.author, "guild_permissions", None)
        return bool(perms and perms.administrator)

    @welcome.command(name="setup")
    @help_meta(
        usage="`.welcome setup <#channel> [message]`",
        desc="Configures the welcome announcement channel and optional greeting text template.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".welcome setup #welcome Welcome {user} to the server!"],
        params=[
            {"name": "channel", "type": "channel", "required": True, "desc": "Channel where welcome cards should be sent."},
            {"name": "message", "type": "str", "required": False, "desc": "Optional text greeting. `{user}` formats as member mention."},
        ],
        note="Requires Administrator or Manage Server permission.",
    )
    async def welcome_setup(self, ctx: commands.Context, channel: discord.TextChannel = None, *, message: str = None):
        if not await self._admin(ctx):
            return await ctx.send("-# admin only")
        if channel is None:
            return await ctx.send("-# usage: `.welcome setup #channel [message]`")
        state = load_json(WELCOME_FILE) or {}
        state[str(ctx.guild.id)] = {
            "channel_id": str(channel.id),
            "message": (message.strip()[:500] if message else None),
        }
        save_json(WELCOME_FILE, state)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    @welcome.command(name="off")
    @help_meta(
        usage="`.welcome off`",
        desc="Disables welcome cards in the server.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".welcome off"],
        params=[],
        note="Requires Administrator permission.",
    )
    async def welcome_off(self, ctx: commands.Context):
        if not await self._admin(ctx):
            return await ctx.send("-# admin only")
        state = load_json(WELCOME_FILE) or {}
        if state.pop(str(ctx.guild.id), None):
            save_json(WELCOME_FILE, state)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    @welcome.command(name="status")
    @help_meta(
        usage="`.welcome status`",
        desc="Shows whether welcome cards are enabled and which channel is configured.",
        section="Server Management",
        perm_tier="public",
        examples=[".welcome status"],
        params=[],
        note="Available to all members.",
    )
    async def welcome_status(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        conf = (load_json(WELCOME_FILE) or {}).get(str(ctx.guild.id))
        if not conf:
            return await ctx.send("-# welcome cards are off. `.welcome setup #channel` to turn on.")
        ch = ctx.guild.get_channel(int(conf["channel_id"]))
        await ctx.send(f"-# welcome cards on in {ch.mention if ch else conf['channel_id']}.")

    @welcome.command(name="test")
    @commands.cooldown(1, 10, commands.BucketType.user)
    @help_meta(
        usage="`.welcome test`",
        desc="Renders a live preview of the server welcome card using your profile avatar and banner.",
        section="Server Management",
        perm_tier="public",
        examples=[".welcome test"],
        params=[],
        note="Does not modify server settings. Rate-limited to 1 use per 10 seconds.",
    )
    async def welcome_test(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        avatar_bytes, banner_bytes = await _fetch_member_art(ctx.author)
        buf = await asyncio.to_thread(
            _render_welcome_card,
            avatar_bytes,
            banner_bytes,
            ctx.guild.name,
            ctx.author.display_name,
            ctx.guild.member_count or len(ctx.guild.members),
        )
        await ctx.send(file=discord.File(fp=buf, filename="welcome.png"))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        conf = (load_json(WELCOME_FILE) or {}).get(str(member.guild.id))
        if not conf:
            return
        channel = member.guild.get_channel(int(conf["channel_id"]))
        if channel is None:
            return

        avatar_bytes, banner_bytes = await _fetch_member_art(member)

        buf = await asyncio.to_thread(
            _render_welcome_card,
            avatar_bytes,
            banner_bytes,
            member.guild.name,
            member.display_name,
            member.guild.member_count or len(member.guild.members),
        )
        text = (conf.get("message") or "").replace("{user}", member.mention)
        try:
            if text:
                await channel.send(text, file=discord.File(fp=buf, filename="welcome.png"))
            else:
                await channel.send(file=discord.File(fp=buf, filename="welcome.png"))
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
