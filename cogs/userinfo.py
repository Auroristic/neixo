"""
cogs/userinfo.py  —  user info image card (.userinfo / .ui)
"""

import asyncio
import io
import logging
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands

from utils import help_meta

log = logging.getLogger(__name__)

COG_META = {
    "category": "general",
    "label": "General",
    "desc": "User info cards.",
}

_STATUS = {
    discord.Status.online: ("online", (87, 242, 135)),
    discord.Status.idle: ("idle", (250, 166, 26)),
    discord.Status.dnd: ("dnd", (237, 66, 69)),
    discord.Status.offline: ("offline", (116, 127, 141)),
}


def _load_fonts():
    from cogs.serverstats import _load_font
    return {
        "title": _load_font(40, bold=True),
        "sub": _load_font(22, bold=False),
        "label": _load_font(22, bold=True),
        "value": _load_font(22, bold=False),
        "small": _load_font(18, bold=False),
    }


def _circle(img_bytes: bytes, size: int):
    from cogs.serverstats import _circle_avatar
    return _circle_avatar(img_bytes, size)


def _render_user_card(
    avatar_bytes: bytes | None,
    member: discord.Member | None,
    user: discord.User,
    member_number: int | None,
    roles_text: str,
    nick: str | None,
    boost: bool,
) -> io.BytesIO:
    from PIL import Image, ImageDraw, ImageFilter

    W, H = 900, 1100
    if avatar_bytes:
        try:
            base = Image.open(io.BytesIO(avatar_bytes)).convert("RGB")
        except Exception:
            base = Image.new("RGB", (W, H), (30, 30, 40))
    else:
        base = Image.new("RGB", (W, H), (30, 30, 40))
    bg = base.resize((W, H), Image.Resampling.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(45))
    bg = Image.blend(bg, Image.new("RGB", (W, H), (20, 20, 25)), 0.7)

    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        gd.line([(0, y), (W, y)], fill=(0, 0, 0, int(80 * (y / H))))
    bg = Image.alpha_composite(bg.convert("RGBA"), grad)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad = 50
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=35, fill=(255, 255, 255, 14))
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=35, outline=(255, 255, 255, 45), width=1)
    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    f = _load_fonts()
    av_size = 170
    if avatar_bytes:
        try:
            av = _circle(avatar_bytes, av_size)
            bg.paste(av, (90, 80), av)
        except Exception:
            pass

    x0 = 90 + av_size + 40
    draw.text((x0, 100), user.display_name, font=f["title"], fill=(255, 255, 255, 255))
    status, sc = _STATUS.get(getattr(member, "status", None), _STATUS[discord.Status.offline])
    if member:
        draw.ellipse([x0, 168, x0 + 16, 184], fill=sc)
        draw.text((x0 + 28, 160), status, font=f["sub"], fill=(255, 255, 255, 170))
    draw.text((x0, 210), f"@{user.name} · {user.id}", font=f["sub"], fill=(255, 255, 255, 170))

    draw.line([(90, 290), (W - 90, 290)], fill=(255, 255, 255, 60), width=1)

    def _row(y, label, value):
        draw.text((90, y), label, font=f["label"], fill=(255, 255, 255, 120))
        draw.text((320, y), value, font=f["value"], fill=(255, 255, 255, 235))

    y = 330
    _row(y, "account created", user.created_at.strftime("%b %d, %Y"))
    y += 55
    _row(y, "joined", member.joined_at.strftime("%b %d, %Y") if member and member.joined_at else "-")
    y += 55
    _row(y, "member #", str(member_number) if member_number else "-")
    y += 55
    _row(y, "nickname", nick or "-")
    y += 55
    _row(y, "roles", roles_text)
    y += 55
    _row(y, "server boosting", "yes" if boost else "no")

    if member and member.activity:
        y += 60
        draw.text((90, y), f"playing: {member.activity.name[:60]}", font=f["sub"], fill=(255, 255, 255, 170))

    footer_y = H - 120
    draw.line([(90, footer_y), (W - 90, footer_y)], fill=(255, 255, 255, 50), width=1)
    draw.text((90, footer_y + 25), user.name, font=f["sub"], fill=(255, 255, 255, 160))

    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG", quality=92)
    buf.seek(0)
    return buf


class UserInfo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="userinfo", aliases=["ui"])
    @help_meta(
        usage="`.userinfo [@user]`",
        desc="Shows a user info card with join dates, roles, and status.",
        section="General",
        examples=[".userinfo", ".userinfo @someone"],
        params=[
            {
                "name": "user",
                "type": "discord.User",
                "required": False,
                "desc": "The user to show. Defaults to you.",
            },
        ],
        note="alias: `.ui`",
    )
    async def userinfo(self, ctx: commands.Context, user: discord.User = None):
        target = user or ctx.author
        member = ctx.guild.get_member(target.id) if ctx.guild else None

        avatar_bytes = None
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(target.display_avatar.url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        avatar_bytes = await r.read()
        except Exception:
            pass

        member_number = None
        roles_text = "-"
        nick = None
        boost = False
        if member:
            roles = [r for r in member.roles if r != member.guild.default_role]
            roles_text = f"{len(roles)} ({member.top_role.name})" if roles else "none"
            nick = member.nick
            boost = bool(member.premium_since)
            try:
                ordered = sorted(member.guild.members, key=lambda m: m.joined_at or datetime.min.replace(tzinfo=timezone.utc))
                member_number = ordered.index(member) + 1
            except Exception:
                member_number = None

        buf = await asyncio.to_thread(
            _render_user_card,
            avatar_bytes,
            member,
            target,
            member_number,
            roles_text,
            nick,
            boost,
        )
        await ctx.send(file=discord.File(fp=buf, filename="userinfo.png"))


async def setup(bot: commands.Bot):
    await bot.add_cog(UserInfo(bot))
