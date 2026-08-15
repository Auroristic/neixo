"""
cogs/digest.py  —  weekly server digest image card (top chatters, vc, bumps, growth)
"""

import asyncio
import io
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord.ext import commands

from utils import DATA_DIR, help_meta, is_owner_or_creator, load_json, save_json

log = logging.getLogger(__name__)

DIGEST_FILE = f"{DATA_DIR}/digest.json"

COG_META = {
    "category": "admin",
    "label": "Admin",
    "desc": "Weekly server statistics digest and visual summary cards.",
}


def _load_digest() -> dict:
    return load_json(DIGEST_FILE) or {}


def _save_digest(state: dict) -> None:
    save_json(DIGEST_FILE, state)


def _week_start_iso() -> str:
    now = datetime.now(timezone.utc)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.isoformat()


def _truncate_text(font, text: str, max_width: int) -> str:
    if font.getlength(text) <= max_width:
        return text
    ell = "..."
    ell_w = font.getlength(ell)
    if ell_w >= max_width:
        return text[:1] if text else ""
    for i in range(len(text) - 1, 0, -1):
        sub = text[:i]
        if font.getlength(sub) + ell_w <= max_width:
            return sub + ell
    return (text[:1] if text else "") + ell


def _render_digest_card(
    icon_bytes: bytes | None,
    guild_name: str,
    week_label: str,
    msg_total: int,
    vc_str: str,
    bumps_total: int,
    member_growth: int,
    chatters: list[tuple[int, str, int]],
    vc_top: list[tuple[int, str, int]],
    bumper_top: list[tuple[int, str, int]],
) -> io.BytesIO:
    from PIL import Image, ImageDraw, ImageFilter
    from cogs.serverstats import _load_font

    title_font = _load_font(42, bold=True)
    sub_font = _load_font(22, bold=False)
    label_font = _load_font(24, bold=True)
    row_font = _load_font(22, bold=False)
    small_font = _load_font(18, bold=False)

    # Dynamic height calculation to prevent any overflow or overlap
    base_header_h = 320
    sec1_h = (45 + (len(chatters) * 40) + 24) if chatters else 0
    sec2_h = (45 + (len(vc_top) * 40) + 24) if vc_top else 0
    sec3_h = (45 + (len(bumper_top) * 40) + 24) if bumper_top else 0
    footer_h = 90

def _make_glass_backdrop(source_bytes: bytes | None, width: int, height: int, dark_tint: float = 0.72) -> Image.Image:
    """Generate an ultra-fast, smooth frosted glass backdrop from an avatar or banner."""
    if source_bytes:
        try:
            src = Image.open(io.BytesIO(source_bytes)).convert("RGB")
            thumb_w = 180
            thumb_h = max(1, int(180 * height / width))
            thumb = src.resize((thumb_w, thumb_h), Image.Resampling.BILINEAR)
            blurred = thumb.filter(ImageFilter.GaussianBlur(10))
            bg = blurred.resize((width, height), Image.Resampling.BICUBIC)
        except Exception:
            bg = Image.new("RGB", (width, height), (14, 15, 18))
    else:
        bg = Image.new("RGB", (width, height), (14, 15, 18))

    overlay = Image.new("RGB", (width, height), (12, 13, 16))
    bg = Image.blend(bg, overlay, dark_tint)

    grad = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(height):
        alpha = int(75 * (y / height))
        gd.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
    bg = Image.alpha_composite(bg.convert("RGBA"), grad)
    return bg


def _render_digest_card(
    icon_bytes: bytes | None,
    guild_name: str,
    week_label: str,
    msg_total: int,
    vc_str: str,
    bumps_total: int,
    member_growth: int,
    chatters: list[tuple[int, str, int]],
    vc_top: list[tuple[int, str, int]],
    bumper_top: list[tuple[int, str, int]],
) -> io.BytesIO:
    base_header_h = 280
    sec1_h = (45 + (len(chatters) * 40) + 24) if chatters else 0
    sec2_h = (45 + (len(vc_top) * 40) + 24) if vc_top else 0
    sec3_h = (45 + (len(bumper_top) * 40) + 24) if bumper_top else 0
    footer_h = 90

    W = 900
    H = max(1100, base_header_h + sec1_h + sec2_h + sec3_h + footer_h)

    bg = _make_glass_backdrop(icon_bytes, W, H, dark_tint=0.72)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad = 45
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=32, fill=(18, 19, 24, 180))
    cd.rounded_rectangle([pad, pad, W - pad, H - pad], radius=32, outline=(210, 215, 230, 45), width=1)
    cd.line([(pad + 30, pad + 1), (W - pad - 30, pad + 1)], fill=(255, 255, 255, 70), width=1)
    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    title_font.draw(draw, (85, 70), "weekly digest", fill=(255, 255, 255, 255))
    sub_font.draw(draw, (85, 130), f"{guild_name} · {week_label}", fill=(180, 185, 195, 200))
    draw.line([(85, 185), (W - 85, 185)], fill=(255, 255, 255, 35), width=1)

    y = 220
    label_font.draw(draw, (85, y), f"{msg_total:,} messages", fill=(245, 248, 255, 240))
    cw = label_font.getlength(f"+{member_growth:,} members")
    label_font.draw(draw, (W - 85 - cw, y), f"+{member_growth:,} members", fill=(245, 248, 255, 240))
    y += 40
    label_font.draw(draw, (85, y), f"{vc_str} in voice", fill=(245, 248, 255, 240))
    cw = label_font.getlength(f"{bumps_total} bumps")
    label_font.draw(draw, (W - 85 - cw, y), f"{bumps_total} bumps", fill=(245, 248, 255, 240))
    y += 70

    def _section(label, rows, unit):
        nonlocal y
        if not rows:
            return
        label_font.draw(draw, (85, y), label, fill=(160, 165, 175, 180))
        y += 45
        for rank, name, val in rows:
            row_font.draw(draw, (85, y), f"{rank}.", fill=(135, 140, 150, 230))
            v = f"{val:,}{unit}"
            vw = row_font.getlength(v)
            max_name_w = W - 85 - vw - 160
            clean_name = _truncate_text(row_font, str(name or "Unknown"), max_name_w)
            row_font.draw(draw, (135, y), clean_name, fill=(235, 240, 248, 230))
            row_font.draw(draw, (W - 85 - vw, y), v, fill=(200, 205, 215, 220))
            y += 40
        y += 24

    _section("top chatters", chatters, " msgs")
    _section("most in voice", vc_top, "m")
    _section("top bumpers", bumper_top, " bumps")

    footer_y = y + 10
    draw.line([(85, footer_y), (W - 85, footer_y)], fill=(255, 255, 255, 35), width=1)
    small_font.draw(draw, (85, footer_y + 25), "xo", fill=(160, 165, 175, 180))

    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG", quality=92)
    buf.seek(0)
    return buf


def _fmt_vc(secs: int) -> str:
    hours, rem = divmod(secs, 3600)
    mins = rem // 60
    return f"{hours}h {mins:02d}m"


class Digest(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.task: asyncio.Task | None = None

    async def cog_load(self):
        self.task = asyncio.create_task(self._loop())

    def cog_unload(self):
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = None

    async def _loop(self):
        await self.bot.wait_until_ready()
        try:
            while not self.bot.is_closed():
                try:
                    await self._maybe_run()
                except Exception as e:
                    log.warning("digest check error: %s", e)
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    async def _maybe_run(self):
        state = _load_digest()
        if not state:
            return
        now = datetime.now(timezone.utc)
        if now.weekday() != 6:  # sunday
            return
        week_key = _week_start_iso()
        for gid_str, conf in state.items():
            if conf.get("last_run_iso", "") >= week_key:
                continue
            guild = self.bot.get_guild(int(gid_str))
            if guild is None:
                continue
            try:
                await self._run_digest(guild, conf)
                conf["last_run_iso"] = now.isoformat()
                _save_digest(state)
            except Exception as e:
                log.warning("digest failed for %s: %s", gid_str, e)

    def _baselines(self, gid_str: str, conf: dict, update: bool = True) -> tuple[dict, dict, dict]:
        base = conf.setdefault("baselines", {})
        # current totals from the stats db
        from cogs.serverstats import _get_conn as _stats_conn
        msgs = dict(_stats_conn().execute(
            "SELECT user_id, count FROM message_counts WHERE guild_id = ?", (int(gid_str),)
        ).fetchall())
        vc = dict(_stats_conn().execute(
            "SELECT user_id, total_seconds FROM vc_time WHERE guild_id = ?", (int(gid_str),)
        ).fetchall())
        try:
            from cogs.bumps import _get_conn as _bumps_conn
            bumps = dict(_bumps_conn().execute(
                "SELECT user_id, count FROM bump_counts WHERE guild_id = ?", (gid_str,)
            ).fetchall())
        except Exception:
            bumps = {}

        # compute deltas vs baseline, then store new baseline
        delta_msgs: dict[int, int] = {}
        delta_vc: dict[int, int] = {}
        delta_bumps: dict[int, int] = {}
        for uid, cur in msgs.items():
            prev = base.get(str(uid), {}).get("msgs", 0)
            if cur - prev > 0:
                delta_msgs[uid] = cur - prev
        for uid, cur in vc.items():
            prev = base.get(str(uid), {}).get("vc", 0)
            if cur - prev > 0:
                delta_vc[uid] = cur - prev
        for uid, cur in bumps.items():
            prev = base.get(str(uid), {}).get("bumps", 0)
            if cur - prev > 0:
                delta_bumps[int(uid)] = cur - prev

        # update baselines to current totals
        if update:
            for uid, cur in msgs.items():
                base.setdefault(str(uid), {})["msgs"] = cur
            for uid, cur in vc.items():
                base.setdefault(str(uid), {})["vc"] = cur
            for uid, cur in bumps.items():
                base.setdefault(str(uid), {})["bumps"] = cur
        return (delta_msgs, delta_vc, delta_bumps)

    async def _build_digest_file(self, guild: discord.Guild, conf: dict, update: bool) -> io.BytesIO | None:
        gid_str = str(guild.id)
        delta_msgs, delta_vc, delta_bumps = self._baselines(gid_str, conf, update=update)

        def _name(uid):
            m = guild.get_member(uid)
            return m.display_name if m else f"<@{uid}>"

        chatters = sorted(delta_msgs.items(), key=lambda x: -x[1])[:5]
        vc_rows = sorted(delta_vc.items(), key=lambda x: -x[1])[:5]
        bumper_rows = sorted(delta_bumps.items(), key=lambda x: -x[1])[:5]

        member_growth = (guild.member_count or len(guild.members)) - conf.get("member_base", guild.member_count or len(guild.members))

        icon_bytes = None
        try:
            if guild.icon:
                async with aiohttp.ClientSession() as s:
                    async with s.get(guild.icon.url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                        if r.status == 200:
                            icon_bytes = await r.read()
        except Exception:
            pass

        week_label = f"week of {datetime.now(timezone.utc).strftime('%b %d')}"
        return await asyncio.to_thread(
            _render_digest_card,
            icon_bytes,
            guild.name,
            week_label,
            sum(delta_msgs.values()),
            _fmt_vc(sum(delta_vc.values())),
            sum(delta_bumps.values()),
            member_growth,
            [(i + 1, _name(uid), n) for i, (uid, n) in enumerate(chatters)],
            [(i + 1, _name(uid), n // 60) for i, (uid, n) in enumerate(vc_rows)],
            [(i + 1, _name(uid), n) for i, (uid, n) in enumerate(bumper_rows)],
        )

    async def _run_digest(self, guild: discord.Guild, conf: dict):
        buf = await self._build_digest_file(guild, conf, update=True)
        if buf is None:
            return
        channel = guild.get_channel(int(conf["channel_id"]))
        if channel is not None:
            try:
                await channel.send(file=discord.File(fp=buf, filename="digest.png"))
            except discord.HTTPException:
                pass

    @commands.group(name="digest", invoke_without_command=True)
    @help_meta(
        usage="`.digest <#channel>`  ·  `.digest now`  ·  `.digest off`  ·  `.digest status`",
        desc="Automated weekly server analytics card (top chatters, voice activity, bumps, member growth).",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".digest #general", ".digest now", ".digest status", ".digest off"],
        params=[],
        note="Requires Administrator or Manage Server permission. Automatically broadcasts every Sunday.",
    )
    async def digest(self, ctx: commands.Context, channel: discord.TextChannel = None):
        if channel is not None:
            # `.digest #channel` turns it on directly (same as `.digest set`)
            return await self.digest_set(ctx, channel)
        await ctx.send("-# digest commands: `.digest <#channel>` · `.digest now` · `.digest off` · `.digest status`")

    async def _admin(self, ctx) -> bool:
        if ctx.guild is None:
            return False
        if is_owner_or_creator(ctx):
            return True
        perms = getattr(ctx.author, "guild_permissions", None)
        return bool(perms and perms.administrator)

    @digest.command(name="off")
    @help_meta(
        usage="`.digest off`",
        desc="Disables scheduled weekly digest cards.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".digest off"],
        params=[],
        note="Requires Administrator permission.",
    )
    async def digest_off(self, ctx: commands.Context):
        if not await self._admin(ctx):
            return await ctx.send("-# admin only")
        state = _load_digest()
        if state.pop(str(ctx.guild.id), None):
            _save_digest(state)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    @digest.command(name="status")
    @help_meta(
        usage="`.digest status`",
        desc="Shows whether weekly digest cards are active and where they post.",
        section="Server Management",
        perm_tier="public",
        examples=[".digest status"],
        params=[],
        note="Available to all members.",
    )
    async def digest_status(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        conf = _load_digest().get(str(ctx.guild.id))
        if not conf:
            return await ctx.send("-# digest is off. `.digest #channel` to turn on.")
        ch = ctx.guild.get_channel(int(conf["channel_id"]))
        await ctx.send(f"-# weekly digest on, posting to {ch.mention if ch else conf['channel_id']} every sunday.")

    @digest.command(name="now")
    @help_meta(
        usage="`.digest now`",
        desc="Generates and sends an on-demand preview of this week's server digest card.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".digest now"],
        params=[],
        note="Requires Administrator permission. Does not reset current weekly tracking baselines.",
    )
    async def digest_now(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if not await self._admin(ctx):
            return await ctx.send("-# admin only")
        conf = _load_digest().get(str(ctx.guild.id)) or {}
        buf = await self._build_digest_file(ctx.guild, conf, update=False)
        if buf is None:
            return
        await ctx.send(file=discord.File(fp=buf, filename="digest.png"))

    @digest.command(name="set", aliases=["on"])
    @help_meta(
        usage="`.digest set <#channel>`",
        desc="Enables the weekly digest card and sets the destination announcement channel.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".digest set #general", ".digest on #announcements"],
        params=[{"name": "channel", "type": "channel", "required": True, "desc": "Channel where digest cards will be published every Sunday."}],
        note="Requires Administrator or Manage Server permission.",
    )
    async def digest_set(self, ctx: commands.Context, channel: discord.TextChannel = None):
        if not await self._admin(ctx):
            return await ctx.send("-# admin only")
        if channel is None:
            return await ctx.send("-# usage: `.digest #channel`")
        state = _load_digest()
        gid = str(ctx.guild.id)
        state[gid] = {
            "channel_id": str(channel.id),
            "baselines": {},
            "member_base": ctx.guild.member_count or len(ctx.guild.members),
            "last_run_iso": "",
        }
        self._baselines(gid, state[gid], update=True)
        _save_digest(state)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")


async def setup(bot: commands.Bot):
    await bot.add_cog(Digest(bot))
