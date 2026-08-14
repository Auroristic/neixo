from __future__ import annotations

import logging
from typing import Any

import discord
from discord.ext import commands

from utils import get_aliases, get_config, get_embed_color, help_meta, is_owner_or_creator

log = logging.getLogger(__name__)

# ── Category & Section icon mapping (Monochrome Unicode) ───────────

_CAT_ICONS: dict[str, str] = {
    "music": "\u266A",          # ♪
    "theme": "\u2605",          # ★
    "admin": "\u25C6",          # ◆
    "moderation": "\u2694",     # ⚔
    "fun": "\u2662",            # ♢
    "profile": "\u25CF",        # ●
    "utility": "\u25A0",        # ■
    "miscellaneous": "\u25AA",  # ▪
    "misc": "\u25AA",           # ▪
    "help": "\u2726",           # ✦
    "setup": "\u2699",          # ⚙
    "support": "\u2665",        # ♥
    "server": "\u25BC",         # ▼
    "ai": "\u2726",             # ✦
    "imagine": "\u2726",        # ✦
    "image": "\u2726",          # ✦
    "media": "\u25CB",          # ○
    "vanity": "\u25C6",         # ◆
    "reminders": "\u25CF",      # ●
    "reactions": "\u2665",      # ♥
    "confessions": "\u270E",    # ✎
    "check": "\u2713",          # ✓
    "serverstats": "\u25A3",    # ▧
    "gifs": "\u25CB",           # ○
    "memory": "\u25C8",         # ◈
    "config": "\u2699",         # ⚙
    "leveling": "\u25B2",       # ▲
    "staff": "\u2605",          # ★
    "general": "\u2606",        # ☆
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
    return _CAT_ICONS.get(cat_label.lower(), "\u25AA")


def _sec_icon(sec_label: str) -> str:
    return _SEC_ICONS.get(sec_label.lower(), "\u00B7")


# ── permission helpers ────────────────────────────────────────

def _can_see(d: dict[str, Any], is_owner: bool, is_wl: bool, has_admin: bool) -> bool:
    perm_tier = d.get("perm_tier", "public")
    if perm_tier in ("creator", "owner") or d.get("owner"):
        return is_owner
    if perm_tier == "guild_owner":
        return is_owner or has_admin
    if perm_tier in ("whitelist", "staff") or d.get("staff"):
        return is_owner or is_wl
    if perm_tier == "admin" or d.get("admin"):
        return is_owner or has_admin
    return True


# ── runtime metadata collector ────────────────────────────────

def _collect(bot: commands.Bot, is_owner: bool, is_wl: bool, has_admin: bool):
    categories: dict[str, Any] = {}
    cmd_index: dict[str, Any] = {}
    seen_metas: set[int] = set()

    import sys

    from utils import get_help_meta

    def _process_command(cmd, cat_id, cat_label):
        meta = get_help_meta(cmd)
        if meta is None:
            return

        cmd_owner = meta.get("owner", False)
        cmd_staff = meta.get("staff", False)
        cmd_admin = meta.get("admin", False)
        perm_tier = meta.get("perm_tier", "public")
        discord_perms = meta.get("discord_perms", [])

        d = {
            "owner": cmd_owner,
            "staff": cmd_staff,
            "admin": cmd_admin,
            "perm_tier": perm_tier,
            "discord_perms": discord_perms,
        }
        if not _can_see(d, is_owner, is_wl, has_admin):
            return

        cmd_name = cmd.qualified_name
        desc = meta.get("desc") or cmd.help or "No description."
        usage = meta.get("usage") or (f"`.{cmd_name} {cmd.signature}`" if cmd.signature else f"`.{cmd_name}`")
        aliases = cmd.aliases
        sec = meta.get("section") or cat_label
        params = meta.get("params") or []
        examples = meta.get("examples") or []
        note = meta.get("note")

        # Extract subcommand list if Group
        subcmds = []
        if isinstance(cmd, commands.Group):
            for sc in cmd.commands:
                sc_meta = get_help_meta(sc) or {}
                sc_desc = sc_meta.get("desc") or sc.help or "No description."
                sc_usage = sc_meta.get("usage") or f".{sc.qualified_name}"
                subcmds.append({
                    "name": sc.name,
                    "qualified_name": sc.qualified_name,
                    "usage": sc_usage,
                    "desc": sc_desc,
                })

        d.update({
            "name": cmd_name,
            "desc": desc,
            "usage": usage,
            "aliases": aliases,
            "section": sec,
            "params": params,
            "examples": examples,
            "note": note,
            "subcommands": subcmds,
            "_cat_label": cat_label,
            "_cat_id": cat_id,
        })

        categories[cat_id]["sections"].setdefault(sec, [])
        categories[cat_id]["sections"][sec].append((cmd_name, d))
        cmd_index[cmd_name] = d

        if isinstance(cmd, commands.Group):
            for subcmd in cmd.commands:
                _process_command(subcmd, cat_id, cat_label)

    for cog in bot.cogs.values():
        meta: dict | None = getattr(cog.__class__, "COG_META", None)
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
        cat_admin = meta.get("admin", False)

        if cat_owner and not is_owner:
            continue
        if cat_admin and not (is_owner or has_admin):
            continue
        if cat_staff and not (is_owner or is_wl):
            continue

        if cat_id not in categories:
            categories[cat_id] = {
                "label": cat_label,
                "desc": cat_desc,
                "staff": cat_staff,
                "owner": cat_owner,
                "admin": cat_admin,
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
    d: dict[str, Any],
    cat_label: str = "",
    sec_label: str = "",
) -> discord.Embed:
    cat_icon = _cat_icon(cat_label or d.get("_cat_id", ""))
    sec_icon = _sec_icon(sec_label) if sec_label else "\u00B7"

    if cat_label and sec_label and cat_label != sec_label:
        title = f"{cat_icon} {cat_label}  \u203A  {sec_label}  \u203A  .{cmd_name}"
    elif cat_label:
        title = f"{cat_icon} {cat_label}  \u203A  .{cmd_name}"
    else:
        title = f"\u2726 .{cmd_name}"

    embed = discord.Embed(color=color)
    avatar_url = bot.user.display_avatar.url if bot.user else None
    embed.set_author(name=title, icon_url=avatar_url)

    # 1. Syntax / Usage
    usage = d.get("usage", f"`.{cmd_name}`")
    clean_usage = usage.strip("` ")
    embed.add_field(name="Syntax", value=f"```ini\n{clean_usage}\n```", inline=False)

    # 2. Description
    embed.add_field(name="Description", value=d.get("desc", "No description."), inline=False)

    # 3. Parameters
    params = d.get("params") or []
    if params:
        param_lines = []
        for p in params:
            p_name = p.get("name", "arg")
            p_type = p.get("type", "str")
            p_req = p.get("required", False)
            p_desc = p.get("desc", "")
            req_tag = "required" if p_req else "optional"
            def_str = f" (default: `{p.get('default')}`)" if "default" in p and not p_req else ""
            param_lines.append(f"\u2022 `<{p_name}>` *({p_type}, {req_tag})*{def_str} \u2014 {p_desc}")
        embed.add_field(name="Parameters", value="\n".join(param_lines), inline=False)

    # 4. Examples
    examples = d.get("examples") or []
    if examples:
        ex_lines = [f"\u2022 `{ex}`" if not ex.startswith("`") else f"\u2022 {ex}" for ex in examples]
        embed.add_field(name="Examples", value="\n".join(ex_lines), inline=False)

    # 5. Subcommands (if group)
    subcommands = d.get("subcommands") or []
    if subcommands:
        sub_lines = []
        for sc in subcommands[:8]:  # show up to 8
            sub_lines.append(f"\u2022 `.{sc['qualified_name']}` \u2014 {sc.get('desc', '')}")
        if len(subcommands) > 8:
            sub_lines.append(f"*...and {len(subcommands) - 8} more subcommands*")
        embed.add_field(name="Subcommands", value="\n".join(sub_lines), inline=False)

    # 6. Aliases
    aliases = d.get("aliases") or []
    if aliases:
        embed.add_field(name="Aliases", value="  \u00B7  ".join(f"`{a}`" for a in aliases), inline=True)

    # 7. Permissions & Requirements
    perm_tier = d.get("perm_tier", "public")
    discord_perms = d.get("discord_perms") or []
    perms_display = []

    if perm_tier == "creator" or d.get("owner"):
        perms_display.append("\u265B Creator Only")
    elif perm_tier == "guild_owner":
        perms_display.append("\u2655 Server Owner Only")
    elif perm_tier in ("whitelist", "staff") or d.get("staff"):
        perms_display.append("\u2726 Whitelisted")
    elif perm_tier == "admin" or d.get("admin"):
        perms_display.append("\u2694 Administrator")
    else:
        perms_display.append("\u2713 Public")

    if discord_perms:
        formatted_perms = ", ".join(p.replace("_", " ").title() for p in discord_perms)
        perms_display.append(f"\u2022 Requires: `{formatted_perms}`")

    embed.add_field(name="Permissions", value="\n".join(perms_display), inline=True)

    # 8. Note / Tips
    if d.get("note"):
        embed.add_field(name="Note", value=f"\u25C8 {d['note']}", inline=False)

    embed.set_footer(text="Neixo \u00B7 Use .help <command> or visit auroristic.github.io/xo", icon_url=avatar_url)
    return embed


# ── cog ───────────────────────────────────────────────────────

COG_META = {
    "category": "help",
    "label": "Help",
    "desc": "Help and command reference system.",
}

class HelpCog(commands.Cog, name="Help"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="help", aliases=["h"])
    @help_meta(
        usage="`.help [command]`",
        desc="Shows the full command catalogue or detailed info for a specific command.",
        section="General",
        examples=[".help", ".help play", ".help remind"],
        params=[
            {"name": "command", "type": "str", "required": False, "desc": "Optional command name to get detailed info about."},
        ],
        note="Without arguments, shows a link to the web help site. Use `.help <command>` for detailed info.",
    )
    async def help_command(self, ctx: commands.Context, *, command: str = None):
        config = get_config()
        guild_id = ctx.guild.id if ctx.guild else 0
        guild_config = config.get(str(guild_id), {})
        whitelist = guild_config.get("whitelist", [])
        is_owner = is_owner_or_creator(ctx)
        is_wl = str(ctx.author.id) in {str(uid) for uid in whitelist}
        perms = getattr(ctx.author, "guild_permissions", None)
        has_admin = perms.administrator if perms and ctx.guild else False
        if not ctx.guild:
            is_wl = False
            has_admin = False
        color = get_embed_color(guild_id)

        if command:
            cmd = command.lower().lstrip(".")
            _, cmd_index = _collect(self.bot, is_owner, is_wl, has_admin)

            if cmd in cmd_index:
                d = cmd_index[cmd]
                cat_label = d.get("_cat_label", "")
                sec_label = d.get("section", "")
                embed = _build_detail_embed(self.bot, color, cmd, d, cat_label, sec_label)
                return await ctx.send(embed=embed)

            # custom aliases aren't registered commands — show the real
            # command's help instead of "not found"
            cmd = get_aliases().get(cmd, cmd)

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

        await ctx.send(
            f"{ctx.author.mention} **https://auroristic.github.io/xo/** for all commands,\n"
            f"use `.help <command>` for specific command or ping n ask me"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
