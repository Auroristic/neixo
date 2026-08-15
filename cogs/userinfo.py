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
        "title": _load_font(34, bold=True),
        "sub": _load_font(20, bold=False),
        "label": _load_font(20, bold=True),
        "value": _load_font(20, bold=False),
        "small": _load_font(18, bold=False),
        "badge": _load_font(16, bold=True),
    }


def _circle(img_bytes: bytes, size: int):
    from cogs.serverstats import _circle_avatar
    return _circle_avatar(img_bytes, size)


def _render_user_card(
    avatar_bytes: bytes | None,
    member: discord.Member | None,
    user: discord.User,
    member_number: int | None,
    total_members: int | None,
    created_str: str,
    joined_str: str,
    nick: str | None,
    top_role_name: str,
    top_role_color: str,
    role_count: int,
    roles_preview: str,
    boost_str: str,
    msg_count: int,
    vc_str: str,
    current_vc: str | None,
    key_perms: list[str],
    badges: list[str],
    activity_str: str | None,
    warn_count: int,
    server_name: str,
) -> io.BytesIO:
    from PIL import Image, ImageDraw
    from cogs.serverstats import _make_glass_backdrop

    W, H = 900, 1150
    bg = _make_glass_backdrop(avatar_bytes, W, H, dark_tint=0.28, blur_radius=14)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad = 40
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=32, fill=(0, 0, 0, 95))
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=32, outline=(255, 255, 255, 55), width=1)
    cd.line([(pad + 25, pad + 1), (W - pad - 25, pad + 1)], fill=(255, 255, 255, 95), width=1)
    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    f = _load_fonts()

    # ── Header ──
    av_size = 145
    av_x, av_y = 80, 68
    if avatar_bytes:
        try:
            av = _circle(avatar_bytes, av_size)
            bg.paste(av, (av_x, av_y), av)
            draw.ellipse([av_x, av_y, av_x + av_size, av_y + av_size], outline=(255, 255, 255, 55), width=1)
        except Exception:
            pass

    x0 = av_x + av_size + 30
    f["title"].draw(draw, (x0, 72), user.display_name, fill=(255, 255, 255, 255))

    status, sc = _STATUS.get(getattr(member, "status", None), _STATUS[discord.Status.offline])
    draw.ellipse([x0, 122, x0 + 14, 136], fill=sc)
    f["sub"].draw(draw, (x0 + 24, 117), status, fill=(200, 205, 215, 200))
    f["sub"].draw(draw, (x0, 152), f"@{user.name} · {user.id}", fill=(160, 165, 175, 180))

    # Badges pill tags
    if badges:
        bx = x0
        by = 188
        for b in badges[:4]:
            bw = f["badge"].getlength(b) + 16
            draw.rounded_rectangle([bx, by, bx + bw, by + 24], radius=6, fill=(255, 255, 255, 20), outline=(255, 255, 255, 40), width=1)
            f["badge"].draw(draw, (bx + 8, by + 4), b, fill=(235, 240, 250, 230))
            bx += bw + 8

    div1_y = 238
    draw.line([(80, div1_y), (W - 80, div1_y)], fill=(255, 255, 255, 35), width=1)

    # ── Section 1: Membership & Dates ──
    def _field(col_x, val_x, max_w, y, label, val):
        f["label"].draw(draw, (col_x, y), label, fill=(160, 165, 175, 180))
        val_disp = val
        if f["value"].getlength(val_disp) > max_w:
            while val_disp and f["value"].getlength(val_disp + "…") > max_w:
                val_disp = val_disp[:-1]
            val_disp = (val_disp + "…") if val_disp else "…"
        f["value"].draw(draw, (val_x, y), val_disp, fill=(240, 244, 252, 235))

    # 2-column layout
    c1_lbl = 80
    c1_val = 265
    c1_max = 185

    c2_lbl = 490
    c2_val = 665
    c2_max = 160

    y_s1 = 265
    row_gap = 50

    _field(c1_lbl, c1_val, c1_max, y_s1, "created", created_str)
    _field(c2_lbl, c2_val, c2_max, y_s1, "joined", joined_str)

    _field(c1_lbl, c1_val, c1_max, y_s1 + row_gap, "member #", f"#{member_number:,} of {total_members:,}" if member_number and total_members else "-")
    _field(c2_lbl, c2_val, c2_max, y_s1 + row_gap, "nickname", nick or "-")

    _field(c1_lbl, c1_val, c1_max, y_s1 + row_gap * 2, "highest role", f"{top_role_name}")
    _field(c2_lbl, c2_val, c2_max, y_s1 + row_gap * 2, "boosting", boost_str)

    div2_y = y_s1 + row_gap * 3 + 20
    draw.line([(80, div2_y), (W - 80, div2_y)], fill=(255, 255, 255, 35), width=1)

    # ── Section 2: Activity & Engagement ──
    y_s2 = div2_y + 25
    _field(c1_lbl, c1_val, c1_max, y_s2, "messages", f"{msg_count:,} msgs" if msg_count else "0 msgs")
    vc_display = f"🔊 #{current_vc}" if current_vc else (vc_str if vc_str != "0m" else "0m")
    _field(c2_lbl, c2_val, c2_max, y_s2, "voice time", vc_display)

    perms_str = ", ".join(key_perms) if key_perms else "Regular Member"
    _field(c1_lbl, c1_val, c1_max, y_s2 + row_gap, "permissions", perms_str)
    _field(c2_lbl, c2_val, c2_max, y_s2 + row_gap, "warnings", f"{warn_count} warning{'s' if warn_count != 1 else ''}")

    div3_y = y_s2 + row_gap * 2 + 20
    draw.line([(80, div3_y), (W - 80, div3_y)], fill=(255, 255, 255, 35), width=1)

    # ── Section 3: Roles & Custom Status ──
    y_s3 = div3_y + 25
    f["label"].draw(draw, (80, y_s3), f"roles ({role_count})", fill=(160, 165, 175, 180))
    roles_disp = roles_preview if roles_preview else "none"
    max_rw = W - 80 - 265
    if f["value"].getlength(roles_disp) > max_rw:
        while roles_disp and f["value"].getlength(roles_disp + "…") > max_rw:
            roles_disp = roles_disp[:-1]
        roles_disp = (roles_disp + "…") if roles_disp else "…"
    f["value"].draw(draw, (265, y_s3), roles_disp, fill=(235, 240, 248, 230))

    if activity_str:
        y_s3 += 48
        f["label"].draw(draw, (80, y_s3), "activity", fill=(160, 165, 175, 180))
        act_disp = activity_str
        if f["value"].getlength(act_disp) > max_rw:
            while act_disp and f["value"].getlength(act_disp + "…") > max_rw:
                act_disp = act_disp[:-1]
            act_disp = (act_disp + "…") if act_disp else "…"
        f["value"].draw(draw, (265, y_s3), act_disp, fill=(200, 205, 220, 220))

    # ── Footer ──
    footer_y = H - 100
    draw.line([(80, footer_y), (W - 80, footer_y)], fill=(255, 255, 255, 35), width=1)
    f["sub"].draw(draw, (80, footer_y + 22), f"// {server_name}", fill=(160, 165, 175, 180))
    id_str = f"account: {user.name}"
    id_w = f["sub"].getlength(id_str)
    f["sub"].draw(draw, (W - 80 - id_w, footer_y + 22), id_str, fill=(160, 165, 175, 180))

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
        desc="Shows a comprehensive user info card with stats, join dates, roles, and status.",
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

        def _rel_time(dt: datetime | None) -> str:
            if not dt:
                return "-"
            now = datetime.now(timezone.utc)
            delta = now - dt
            days = delta.days
            if days < 1:
                return f"{dt.strftime('%b %d, %Y')} (today)"
            elif days < 30:
                return f"{dt.strftime('%b %d, %Y')} ({days}d ago)"
            elif days < 365:
                mos = max(1, days // 30)
                return f"{dt.strftime('%b %d, %Y')} ({mos} mo{'s' if mos > 1 else ''} ago)"
            else:
                yrs = days // 365
                rem_mos = (days % 365) // 30
                if rem_mos > 0:
                    return f"{dt.strftime('%b %d, %Y')} ({yrs}y {rem_mos}m ago)"
                return f"{dt.strftime('%b %d, %Y')} ({yrs} yr{'s' if yrs > 1 else ''} ago)"

        created_str = _rel_time(target.created_at)
        joined_str = _rel_time(member.joined_at) if member and member.joined_at else "-"

        member_number = None
        total_members = ctx.guild.member_count if ctx.guild else None
        nick = member.nick if member else None
        top_role_name = "none"
        top_role_color = "#FFFFFF"
        role_count = 0
        roles_preview = ""
        boost_str = "no"

        if member:
            roles = [r for r in member.roles if r != member.guild.default_role]
            role_count = len(roles)
            if roles:
                roles_sorted = sorted(roles, key=lambda r: r.position, reverse=True)
                top_role = roles_sorted[0]
                top_role_name = top_role.name
                top_role_color = f"#{top_role.color.value:06x}" if top_role.color.value else "#FFFFFF"
                roles_preview = ", ".join(r.name for r in roles_sorted[:6])
                if len(roles_sorted) > 6:
                    roles_preview += f", +{len(roles_sorted) - 6} more"
            if member.premium_since:
                boost_str = f"Active ({member.premium_since.strftime('%b %d, %Y')})"
            try:
                ordered = sorted(member.guild.members, key=lambda m: m.joined_at or datetime.min.replace(tzinfo=timezone.utc))
                member_number = ordered.index(member) + 1
            except Exception:
                member_number = None

        # Stats from serverstats.db
        msg_count = 0
        vc_seconds = 0
        if ctx.guild:
            try:
                import sqlite3
                from utils import DATA_DIR
                db_path = f"{DATA_DIR}/serverstats.db"
                with sqlite3.connect(db_path, timeout=3) as conn:
                    row = conn.execute(
                        "SELECT count FROM message_counts WHERE guild_id = ? AND user_id = ?",
                        (ctx.guild.id, target.id)
                    ).fetchone()
                    if row:
                        msg_count = row[0]
                    row_vc = conn.execute(
                        "SELECT total_seconds FROM vc_time WHERE guild_id = ? AND user_id = ?",
                        (ctx.guild.id, target.id)
                    ).fetchone()
                    if row_vc:
                        vc_seconds = row_vc[0]
            except Exception:
                pass

        # VC time formatting
        if vc_seconds >= 3600:
            h = vc_seconds // 3600
            m = (vc_seconds % 3600) // 60
            vc_str = f"{h}h {m}m"
        else:
            m = vc_seconds // 60
            vc_str = f"{m}m"

        current_vc = member.voice.channel.name if member and member.voice and member.voice.channel else None

        # Warnings count
        warn_count = 0
        if ctx.guild:
            try:
                from cogs.warns import _load_warns
                warns_data = _load_warns()
                g_warns = warns_data.get(str(ctx.guild.id), {})
                u_warns = g_warns.get(str(target.id), [])
                warn_count = len(u_warns)
            except Exception:
                pass

        # Key Permissions
        key_perms = []
        if member:
            perms = member.guild_permissions
            if perms.administrator:
                key_perms.append("Administrator")
            else:
                if perms.manage_guild: key_perms.append("Manage Server")
                if perms.manage_roles: key_perms.append("Manage Roles")
                if perms.manage_channels: key_perms.append("Manage Channels")
                if perms.manage_messages: key_perms.append("Manage Messages")
                if perms.ban_members: key_perms.append("Ban Members")
                if perms.kick_members: key_perms.append("Kick Members")
                if perms.moderate_members: key_perms.append("Moderate Members")

        # Badges / Flags
        badges = []
        flags = target.public_flags
        if flags.staff: badges.append("Discord Staff")
        if flags.partner: badges.append("Partner")
        if flags.hypesquad_bravery: badges.append("HypeSquad Bravery")
        elif flags.hypesquad_brilliance: badges.append("HypeSquad Brilliance")
        elif flags.hypesquad_balance: badges.append("HypeSquad Balance")
        if flags.early_supporter: badges.append("Early Supporter")
        if flags.active_developer: badges.append("Active Developer")
        if flags.verified_bot: badges.append("Verified Bot")
        elif target.bot: badges.append("Bot")
        if member and member.premium_since: badges.append("Server Booster")

        # Activity String
        activity_str = None
        if member and member.activities:
            for act in member.activities:
                if isinstance(act, discord.Spotify):
                    activity_str = f"Spotify: {act.title} - {act.artist}"
                    break
                elif isinstance(act, discord.CustomActivity):
                    if act.name:
                        activity_str = f"Status: {act.name}"
                        break
                elif isinstance(act, discord.Game):
                    activity_str = f"Playing: {act.name}"
                    break
                elif getattr(act, "name", None):
                    activity_str = f"Playing: {act.name}"
                    break

        server_name = ctx.guild.name if ctx.guild else "Direct Messages"

        buf = await asyncio.to_thread(
            _render_user_card,
            avatar_bytes,
            member,
            target,
            member_number,
            total_members,
            created_str,
            joined_str,
            nick,
            top_role_name,
            top_role_color,
            role_count,
            roles_preview,
            boost_str,
            msg_count,
            vc_str,
            current_vc,
            key_perms,
            badges,
            activity_str,
            warn_count,
            server_name,
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
