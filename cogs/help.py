from __future__ import annotations

import logging

import discord
from discord.ext import commands
from typing import Any, Dict, Optional

from utils import load_json, get_embed_color, is_owner_or_creator, CONFIG_FILE

log = logging.getLogger(__name__)

# ── Category & Section icon mapping ───────────────────────────────

_CAT_ICONS: dict[str, str] = {
    "music": "\u266A",
    "theme": "\u2605",
    "admin": "\u25C6",
    "moderation": "\u2694",
    "fun": "\u2662",
    "profile": "\u25CF",
    "utility": "\u25A0",
    "miscellaneous": "\u25AA",
    "misc": "\u25AA",
    "help": "?",
    "setup": "\u2699",
    "support": "\u2665",
    "server": "\u25BC",
    "ai": "\u25B2",
    "imagine": "\u2726",
    "vanity": "\u25C6",
    "reminders": "\u25CF",
    "reactions": "\u2665",
    "confessions": "\u270E",
    "check": "\u2713",
    "serverstats": "\u25A3",
    "funimage": "\u2726",
    "gifs": "\u25CB",
    "memory": "\u25C8",
    "config": "\u2699",
    "general": "\u2606",
}

_SEC_ICONS: dict[str, str] = {
    "playback": "\u25BA",
    "controls": "\u25C8",
    "filters": "\u25C7",
    "queue": "\u2261",
    "overview": "\u25C9",
    "setup": "\u2699",
    "roles": "\u25CE",
    "channels": "\u25C7",
    "presets": "\u25A0",
    "user": "\u25CF",
    "rpc": "\u25C6",
    "moderation": "\u2694",
    "config": "\u2699",
    "general": "\u2606",
    "image": "\u2726",
    "text": "\u270E",
    "anime": "\u25CE",
    "games": "\u2662",
    "quotes": "\u275D",
    "owner": "\u265B",
    "server": "\u25BC",
    "member": "\u25CF",
    "commands": "\u25C6",
    "voice": "\u266A",
    "messages": "\u2709",
    "info": "\u2139",
    "manage": "\u2699",
    "utilities": "\u25A0",
    "search": "\u25C7",
    "stats": "\u25A3",
    "ai": "\u25B2",
    "image gen": "\u2726",
    "vanity": "\u25C6",
    "reminders": "\u25CF",
    "birthdays": "\u2606",
    "confessions": "\u270E",
    "reactions": "\u2665",
    "checks": "\u2713",
    "server stats": "\u25A3",
    "verification": "\u2713",
    "notes": "\u270E",
    "blacklist": "\u2715",
    "whispers": "\u2665",
    "command channels": "\u25C6",
    "captcha": "\u25B2",
    "suggestions": "\u2709",
}


def _cat_icon(cat_label: str) -> str:
    return _CAT_ICONS.get(cat_label.lower(), "\u25aa")


def _sec_icon(sec_label: str) -> str:
    return _SEC_ICONS.get(sec_label.lower(), "\u00b7")


# ── permission helpers ────────────────────────────────────────

def _can_see(d: Dict[str, Any], is_owner: bool, is_wl: bool) -> bool:
    if d.get("owner") and not is_owner:
        return False
    if d.get("admin") and not is_owner:
        return False
    if d.get("staff") and not (is_owner or is_wl):
        return False
    return True


# ── runtime metadata collector ────────────────────────────────

def _collect(bot: commands.Bot, is_owner: bool, is_wl: bool):
    categories: Dict[str, Any] = {}
    cmd_index: Dict[str, Any] = {}
    seen_metas: set[int] = set()

    import sys
    from utils import get_help_meta

    def _process_command(cmd, cat_id, cat_label):
        meta = get_help_meta(cmd)
        if meta is None:
            return

        cmd_owner = meta.get("owner", False)
        cmd_staff = meta.get("staff", False)

        d = {
            "owner": cmd_owner,
            "staff": cmd_staff,
        }
        if not _can_see(d, is_owner, is_wl):
            return

        cmd_name = cmd.qualified_name
        desc = meta.get("desc") or cmd.help or "No description."
        usage = meta.get("usage") or (f"`.{cmd_name} {cmd.signature}`" if cmd.signature else f"`.{cmd_name}`")
        aliases = cmd.aliases

        sec = meta.get("section") or cat_label

        d.update({
            "desc": desc,
            "usage": usage,
            "aliases": aliases,
            "section": sec,
            "_cat_label": cat_label,
        })

        categories[cat_id]["sections"].setdefault(sec, [])
        categories[cat_id]["sections"][sec].append((cmd_name, d))
        cmd_index[cmd_name] = d

        if isinstance(cmd, commands.Group):
            for subcmd in cmd.commands:
                _process_command(subcmd, cat_id, cat_label)

    for cog in bot.cogs.values():
        meta: Optional[Dict] = getattr(cog.__class__, "COG_META", None)
        if not meta:
            module = sys.modules.get(cog.__class__.__module__)
            meta = getattr(module, "COG_META", None)
        if not meta or not isinstance(meta, dict):
            continue

        if id(meta) in seen_metas:
            continue
        seen_metas.add(id(meta))

        cat_id = str(meta.get("category", cog.__class__.__name__.lower())).strip()
        cat_label = str(meta.get("label", cat_id.title())).strip()
        cat_desc = str(meta.get("desc", "")).strip()
        cat_staff = meta.get("staff", False)
        cat_owner = meta.get("owner", False)

        if cat_owner and not is_owner:
            continue
        if cat_staff and not (is_owner or is_wl):
            continue

        if cat_id not in categories:
            categories[cat_id] = {
                "label": cat_label,
                "desc": cat_desc,
                "staff": cat_staff,
                "owner": cat_owner,
                "sections": {},
            }

        for cmd in cog.get_commands():
            _process_command(cmd, cat_id, cat_label)

    for cat in categories.values():
        for sec in cat["sections"]:
            cat["sections"][sec].sort(key=lambda x: x[0])

    return categories, cmd_index


# ── embed builder for .help <command> ─────────────────────────

def _build_detail_embed(
    bot: commands.Bot,
    color: int,
    cmd_name: str,
    d: Dict[str, Any],
    cat_label: str = "",
    sec_label: str = "",
) -> discord.Embed:
    icon = _sec_icon(sec_label) if sec_label else ""
    title = f"{icon} {cat_label}  \u203a  {sec_label}  \u203a  .{cmd_name}" if sec_label else f".{cmd_name}"
    embed = discord.Embed(color=color)
    embed.set_author(name=title, icon_url=bot.user.display_avatar.url)
    usage = d.get("usage", f"`.{cmd_name}`")
    embed.add_field(name="Usage", value=f"```ini\n{usage}\n```", inline=False)
    embed.add_field(name="Description", value=d.get("desc", "No description."), inline=False)
    if d.get("aliases"):
        embed.add_field(name="Aliases", value="\u00b7  ".join(f"`{a}`" for a in d["aliases"]), inline=False)
    perms = []
    if d.get("owner"):
        perms.append("\u265b owner only")
    if d.get("admin"):
        perms.append("\u2694 admin only")
    if d.get("staff"):
        perms.append("\u2605 staff only")
    if perms:
        embed.add_field(name="Permissions", value="\n".join(perms), inline=False)
    embed.set_footer(text=".help <command> for more info", icon_url=bot.user.display_avatar.url)
    return embed


# ── simple main embed ─────────────────────────────────────────

def _build_simple_help_embed(
    bot: commands.Bot,
    color: int,
    author: discord.User,
) -> discord.Embed:
    desc = (
        f"{author.mention} **https://auroristic.github.io/xo/** for all commands."
        f"use `.help <command>` for specific command or ping n ask me"
    )
    embed = discord.Embed(description=desc, color=color)
    embed.set_footer(
        text=f"{bot.user.display_name} • .gg/seoulities",
        icon_url=bot.user.display_avatar.url,
    )
    return embed


# ── cog ───────────────────────────────────────────────────────

class HelpCog(commands.Cog, name="Help"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="help", aliases=["h"])
    async def help_command(self, ctx: commands.Context, *, command: str = None):
        config = load_json(CONFIG_FILE)
        guild_id = ctx.guild.id if ctx.guild else 0
        guild_config = config.get(str(guild_id), {})
        whitelist = guild_config.get("whitelist", [])
        is_owner = is_owner_or_creator(ctx)
        is_wl = str(ctx.author.id) in whitelist
        color = get_embed_color(guild_id)

        if command:
            cmd = command.lower().lstrip(".")
            _, cmd_index = _collect(self.bot, is_owner, is_wl)

            if cmd in cmd_index:
                d = cmd_index[cmd]
                cat_label = d.get("_cat_label", "")
                sec_label = d.get("section", "")
                embed = _build_detail_embed(self.bot, color, cmd, d, cat_label, sec_label)
                return await ctx.send(embed=embed)

            real_cmd = self.bot.get_command(cmd)
            if real_cmd:
                embed = discord.Embed(color=color)
                embed.set_author(name=f".{real_cmd.name}", icon_url=self.bot.user.display_avatar.url)
                usage = (f"`.{real_cmd.name} {real_cmd.signature}`"
                         if real_cmd.signature else f"`.{real_cmd.name}`")
                embed.add_field(name="Usage", value=usage, inline=False)
                embed.add_field(name="Description", value=real_cmd.help or "No description.", inline=False)
                if real_cmd.aliases:
                    embed.add_field(name="Aliases",
                                    value=" \u00b7 ".join(f"`{a}`" for a in real_cmd.aliases), inline=False)
                embed.add_field(name="Category", value=real_cmd.cog_name or "Unknown", inline=False)
                return await ctx.send(embed=embed)

            return await ctx.send(
                f"Command `{cmd}` not found. Use `.help` to browse all commands."
            )

        embed = _build_simple_help_embed(self.bot, color, ctx.author)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
