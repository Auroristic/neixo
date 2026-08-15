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
    "category": "profile",
    "label": "Profile",
    "desc": "User profile cards, avatars, banners, and role details.",
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
    from PIL import Image, ImageDraw
    from cogs.serverstats import _make_glass_backdrop

    W, H = 900, 1100
    bg = _make_glass_backdrop(avatar_bytes, W, H, dark_tint=0.32, blur_radius=28)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad = 45
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=32, fill=(0, 0, 0, 95))
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=32, outline=(255, 255, 255, 55), width=1)
    cd.line([(pad + 30, pad + 1), (W - pad - 30, pad + 1)], fill=(255, 255, 255, 95), width=1)
    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    f = _load_fonts()
    av_size = 170
    if avatar_bytes:
        try:
            av = _circle(avatar_bytes, av_size)
            bg.paste(av, (85, 75), av)
            draw.ellipse([85, 75, 85 + av_size, 75 + av_size], outline=(255, 255, 255, 50), width=1)
        except Exception:
            pass

    x0 = 85 + av_size + 40
    f["title"].draw(draw, (x0, 95), user.display_name, fill=(255, 255, 255, 255))
    status, sc = _STATUS.get(getattr(member, "status", None), _STATUS[discord.Status.offline])
    if member:
        draw.ellipse([x0, 163, x0 + 16, 179], fill=sc)
        f["sub"].draw(draw, (x0 + 28, 155), status, fill=(200, 205, 215, 200))
    f["sub"].draw(draw, (x0, 205), f"@{user.name} · {user.id}", fill=(160, 165, 175, 180))

    draw.line([(85, 285), (W - 85, 285)], fill=(255, 255, 255, 35), width=1)

    def _row(y, label, value):
        f["label"].draw(draw, (85, y), label, fill=(160, 165, 175, 180))
        f["value"].draw(draw, (315, y), value, fill=(240, 244, 252, 235))

    y = 325
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
        act_name = getattr(member.activity, "name", "") or getattr(member.activity, "state", "") or ""
        if act_name:
            y += 60
            f["sub"].draw(draw, (85, y), f"playing: {act_name[:60]}", fill=(180, 185, 195, 200))

    footer_y = H - 115
    draw.line([(85, footer_y), (W - 85, footer_y)], fill=(255, 255, 255, 35), width=1)
    f["sub"].draw(draw, (85, footer_y + 25), user.name, fill=(160, 165, 175, 180))

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

    @commands.command(name="avatar", aliases=["av", "pfp"])
    @help_meta(
        usage="`.avatar [@user]`",
        desc="shows full-resolution avatar with server/global toggles and download links",
        section="General",
        examples=[".avatar", ".av @someone"],
        params=[
            {
                "name": "user",
                "type": "discord.User",
                "required": False,
                "desc": "User to fetch avatar for. Defaults to you.",
            },
        ],
        note="aliases: `.av`, `.pfp`",
    )
    async def avatar_cmd(self, ctx: commands.Context, user: discord.User = None):
        target = user or ctx.author
        member = ctx.guild.get_member(target.id) if ctx.guild else None
        view = AvatarView(ctx.author.id, member or target, ctx.guild.id if ctx.guild else 0)
        embed = view.build_embed()
        view.message = await ctx.send(embed=embed, view=view if len(view.children) > 0 else None)

    @commands.command(name="banner", aliases=["userbanner"])
    @help_meta(
        usage="`.banner [@user]`",
        desc="shows user profile banner with high-resolution download links",
        section="General",
        examples=[".banner", ".banner @someone"],
        params=[
            {
                "name": "user",
                "type": "discord.User",
                "required": False,
                "desc": "User to fetch banner for. Defaults to you.",
            },
        ],
        note="alias: `.userbanner`",
    )
    async def banner_cmd(self, ctx: commands.Context, user: discord.User = None):
        target = user or ctx.author
        try:
            full_user = await self.bot.fetch_user(target.id)
        except Exception:
            full_user = target

        from utils import get_embed_color
        color = get_embed_color(ctx.guild.id if ctx.guild else 0)

        if full_user.banner:
            banner_url = full_user.banner.url
            png_url = full_user.banner.replace(format="png", size=4096).url
            webp_url = full_user.banner.replace(format="webp", size=4096).url
            links = f"[png]({png_url}) · [webp]({webp_url})"
            if full_user.banner.is_animated():
                gif_url = full_user.banner.replace(format="gif", size=4096).url
                links += f" · [gif]({gif_url})"

            embed = discord.Embed(
                description=f"-# {full_user.name}'s banner · {links}",
                color=color,
            )
            embed.set_image(url=banner_url)
            await ctx.send(embed=embed)
        elif getattr(full_user, "accent_color", None):
            hex_c = f"#{full_user.accent_color.value:06x}"
            embed = discord.Embed(
                description=f"-# {full_user.name} has no banner (accent: `{hex_c}`)",
                color=full_user.accent_color.value,
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"-# {full_user.name} has no banner")

    @commands.command(name="roleinfo", aliases=["role", "roles"])
    @help_meta(
        usage="`.roleinfo <@role>`",
        desc="shows detailed metadata, members, and key permissions for a role",
        section="General",
        examples=[".roleinfo @Member", ".role @VIP"],
        params=[
            {
                "name": "role",
                "type": "discord.Role",
                "required": True,
                "desc": "The role to inspect.",
            },
        ],
        note="aliases: `.role`, `.roles`",
    )
    async def roleinfo_cmd(self, ctx: commands.Context, *, role: discord.Role = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers")
        if role is None:
            return await ctx.send("-# usage: `.roleinfo <@role>`")

        from utils import get_embed_color
        color = role.color.value if role.color.value != 0 else get_embed_color(ctx.guild.id)
        total_members = len(ctx.guild.members) or 1
        member_count = len(role.members)
        pct = (member_count / total_members) * 100

        key_perms = []
        p = role.permissions
        if p.administrator:
            key_perms.append("administrator")
        if p.manage_guild:
            key_perms.append("manage server")
        if p.manage_roles:
            key_perms.append("manage roles")
        if p.manage_channels:
            key_perms.append("manage channels")
        if p.ban_members:
            key_perms.append("ban members")
        if p.kick_members:
            key_perms.append("kick members")
        if p.manage_messages:
            key_perms.append("manage messages")
        if p.mention_everyone:
            key_perms.append("mention everyone")
        if p.manage_webhooks:
            key_perms.append("manage webhooks")
        if p.manage_expressions:
            key_perms.append("manage emojis")
        if p.mute_members:
            key_perms.append("mute members")
        if p.deafen_members:
            key_perms.append("deafen members")
        if p.move_members:
            key_perms.append("move members")

        perms_str = ", ".join(key_perms) if key_perms else "standard member perms"

        created_ts = int(role.created_at.timestamp())
        hex_color = f"#{role.color.value:06x}" if role.color.value != 0 else "default"

        embed = discord.Embed(
            title=role.name.lower(),
            description=(
                f"-# id: `{role.id}` · mention: {role.mention}\n\n"
                f"**members:** {member_count} ({pct:.1f}%)\n"
                f"**color:** `{hex_color}` · **position:** {role.position}\n"
                f"**hoisted:** {'yes' if role.hoist else 'no'} · **mentionable:** {'yes' if role.mentionable else 'no'}\n"
                f"**key perms:** {perms_str}\n"
                f"**created:** <t:{created_ts}:R>"
            ),
            color=color,
        )
        if role.icon:
            embed.set_thumbnail(url=role.icon.url)
        embed.set_footer(text=f"server: {ctx.guild.name.lower()}")
        await ctx.send(embed=embed)


class AvatarView(discord.ui.View):
    def __init__(self, author_id: int, user: discord.User | discord.Member, guild_id: int):
        super().__init__(timeout=90)
        self.author_id = author_id
        self.user = user
        self.guild_id = guild_id
        self.mode = "server" if (isinstance(user, discord.Member) and user.guild_avatar) else "global"
        self.message: discord.Message | None = None
        self._refresh_items()

    def _refresh_items(self):
        self.clear_items()
        has_server_av = isinstance(self.user, discord.Member) and self.user.guild_avatar is not None
        has_banner = getattr(self.user, "banner", None) is not None

        from neixoconfig import Neixoemojis

        if has_server_av:
            btn_srv = discord.ui.Button(
                label="server",
                emoji=Neixoemojis.get("home", "<:MekoHome:1370292713768878110>"),
                style=discord.ButtonStyle.secondary,
                disabled=(self.mode == "server"),
                custom_id="av_server",
            )
            btn_srv.callback = self._switch_server
            self.add_item(btn_srv)

        btn_glb = discord.ui.Button(
            label="global",
            emoji=Neixoemojis.get("user", "<:user:1372815179242274946>"),
            style=discord.ButtonStyle.secondary,
            disabled=(self.mode == "global"),
            custom_id="av_global",
        )
        btn_glb.callback = self._switch_global
        self.add_item(btn_glb)

        if has_banner:
            btn_bnr = discord.ui.Button(
                label="banner",
                emoji=Neixoemojis.get("category", "<:Category:1370079955333157036>"),
                style=discord.ButtonStyle.secondary,
                disabled=(self.mode == "banner"),
                custom_id="av_banner",
            )
            btn_bnr.callback = self._switch_banner
            self.add_item(btn_bnr)

    def build_embed(self) -> discord.Embed:
        from utils import get_embed_color
        color = get_embed_color(self.guild_id)

        if self.mode == "server" and isinstance(self.user, discord.Member) and self.user.guild_avatar:
            asset = self.user.guild_avatar
            label = "server avatar"
        elif self.mode == "banner" and getattr(self.user, "banner", None):
            asset = self.user.banner
            label = "banner"
        else:
            asset = self.user.avatar or self.user.default_avatar
            label = "global avatar"

        png_url = asset.replace(format="png", size=4096).url
        webp_url = asset.replace(format="webp", size=4096).url
        links = f"[png]({png_url}) · [webp]({webp_url})"
        if asset.is_animated():
            gif_url = asset.replace(format="gif", size=4096).url
            links += f" · [gif]({gif_url})"

        embed = discord.Embed(
            description=f"-# {self.user.name}'s {label} · {links}",
            color=color,
        )
        embed.set_image(url=asset.url)
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("-# only the command author can switch views", ephemeral=True)
            return False
        return True

    async def _switch_server(self, interaction: discord.Interaction):
        self.mode = "server"
        self._refresh_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _switch_global(self, interaction: discord.Interaction):
        self.mode = "global"
        self._refresh_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _switch_banner(self, interaction: discord.Interaction):
        self.mode = "banner"
        self._refresh_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(UserInfo(bot))
