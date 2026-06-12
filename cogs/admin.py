import asyncio

import discord
from discord.ext import commands

from utils import (
    load_json, save_json, get_embed_color, get_config, invalidate_config,
    get_ignore_list, invalidate_ignore, is_owner_or_creator,
    get_aliases, invalidate_aliases,
    CONFIG_FILE, IGNORE_LIST_FILE, ALIASES_FILE,
    get_cmd_channel_rule,
    clear_cmd_channel_rule,
    clear_cmd_channel_rules,
    set_cmd_channel_rule,
    get_cmd_channel_rules,
    help_meta,
)

# ── cogs/admin.py ───────────────────────────────────────────────
COG_META = {
    "category": "admin",
    "label": "Admin",
    "desc": "Server management and AI configuration.",
    "admin": True,
}
 




class AdminCog(commands.Cog, name="Admin"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._config_lock = asyncio.Lock()
        self._aliases_lock = asyncio.Lock()
        self._ignore_lock = asyncio.Lock()

    # ── whitelist ──────────────────────────────────────────────
    @commands.command(name="whitelist")
    @help_meta(usage=".whitelist @user", desc="toggles a user on/off the staff whitelist.", section="Server Management", owner=True)
    async def whitelist(self, ctx, user: discord.Member = None):
        if not is_owner_or_creator(ctx):
            return await ctx.send("owner only")
        
        async with self._config_lock:
            config = load_json(CONFIG_FILE)
            if str(ctx.guild.id) not in config:
                config[str(ctx.guild.id)] = {}
            if 'whitelist' not in config[str(ctx.guild.id)]:
                config[str(ctx.guild.id)]['whitelist'] = []
            
            whitelist_ids = config[str(ctx.guild.id)]['whitelist']
            
            if not user:
                return await ctx.send("`.whitelist @user` to toggle them")
            
            uid = user.id
            if uid in whitelist_ids:
                whitelist_ids.remove(uid)
                save_json(CONFIG_FILE, config)
                invalidate_config()
                await ctx.message.add_reaction("<:redlotus:1263556248310386800>")
            else:
                whitelist_ids.append(uid)
                save_json(CONFIG_FILE, config)
                invalidate_config()
                await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    # ── whitelistshow ──────────────────────────────────────────
    @commands.command(name="whitelistshow")
    @help_meta(usage=".whitelistshow", desc="shows all whitelisted users.", section="Server Management", owner=True)
    async def whitelist_show(self, ctx):
        if not is_owner_or_creator(ctx):
            return await ctx.send("owner only")
        
        async with self._config_lock:
            config = load_json(CONFIG_FILE)
            whitelist_ids = config.get(str(ctx.guild.id), {}).get('whitelist', [])
        
        if not whitelist_ids:
            embed = discord.Embed(description="no one whitelisted yet", color=get_embed_color(ctx.guild.id))
            return await ctx.send(embed=embed)
        
        lines = [f"• <@{uid}>" for uid in whitelist_ids]
        embed = discord.Embed(
            title="antiblack",
            description="\n".join(lines),
            color=get_embed_color(ctx.guild.id)
        )
        await ctx.send(embed=embed)

    # ── setcolor ───────────────────────────────────────────────
    @commands.command(name='setcolor')
    @help_meta(usage=".setcolor #HEX", desc="changes the embed accent colour for this server.", section="Server Management", owner=True)
    async def setcolor(self, ctx, color: str):
        if not is_owner_or_creator(ctx):
            return await ctx.send("no perms")
        
        color = color.strip('#')
        try:
            color_int = int(color, 16) & 0xFFFFFF
        except ValueError:
            return await ctx.send("invalid color, use hex like `#FF0000` or `FF0000`")
        
        async with self._config_lock:
            config = load_json(CONFIG_FILE)
            if str(ctx.guild.id) not in config:
                config[str(ctx.guild.id)] = {}
            config[str(ctx.guild.id)]['embed_color'] = color_int
            save_json(CONFIG_FILE, config)
            invalidate_config()
        embed = discord.Embed(description=f"nya?", color=color_int)
        await ctx.send(embed=embed)

    # ── ignore ─────────────────────────────────────────────────
    @commands.command(name="ignore")
    @help_meta(usage=".ignore @user", desc="toggles ignoring a user — bot won't respond to them in AI channels.", section="Server Management", staff=True)
    async def ignore_user(self, ctx, user: discord.Member = None):
        config = get_config()
        guild_config = config.get(str(ctx.guild.id), {})
        whitelist = guild_config.get('whitelist', [])
        if not is_owner_or_creator(ctx) and str(ctx.author.id) not in whitelist:
            return await ctx.send("no perms")
        if not user:
            return await ctx.send(".ignore @user")
        async with self._ignore_lock:
            ignore_list = load_json(IGNORE_LIST_FILE)
            if user.id not in ignore_list:
                ignore_list.append(user.id)
                save_json(IGNORE_LIST_FILE, ignore_list)
                invalidate_ignore()
                await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
            else:
                ignore_list.remove(user.id)
                save_json(IGNORE_LIST_FILE, ignore_list)
                invalidate_ignore()
                await ctx.message.add_reaction("<:redlotus:1263556248310386800>")

    # ── ignorelist ────────────────────────────────────────────
    @commands.command(name="ignorelist")
    @help_meta(usage=".ignorelist", desc="shows all users currently ignored by the bot in AI channels.", section="Server Management", staff=True)
    async def ignore_list(self, ctx):
        config = get_config()
        guild_config = config.get(str(ctx.guild.id), {})
        whitelist = guild_config.get('whitelist', [])
        if not is_owner_or_creator(ctx) and str(ctx.author.id) not in whitelist:
            return await ctx.send("no perms")
        ignore_list = get_ignore_list()
        if not ignore_list:
            embed = discord.Embed(description="no one is ignored", color=get_embed_color(ctx.guild.id))
            return await ctx.send(embed=embed)
        lines = [f"• <@{uid}>" for uid in ignore_list]
        embed = discord.Embed(
            title="Ignored Users",
            description="\n".join(lines),
            color=get_embed_color(ctx.guild.id)
        )
        await ctx.send(embed=embed)

    # ── confess set ────────────────────────────────────────────
    @commands.command(name="confess")
    @help_meta(usage=".confess set #channel", desc="sets the confession channel.", section="Server Management", admin=True)
    async def confess_prefix(self, ctx, action: str = None, channel: discord.TextChannel = None):
        if action == "set":
            if not is_owner_or_creator(ctx) and not ctx.author.guild_permissions.administrator:
                await ctx.send("admin only")
                return
            
            if not channel:
                await ctx.send("mention a channel. `.confess set #channel`")
                return
            
            async with self._config_lock:
                config = load_json(CONFIG_FILE)
                if str(ctx.guild.id) not in config:
                    config[str(ctx.guild.id)] = {}
                
                config[str(ctx.guild.id)]['confession_channel'] = str(channel.id)
                save_json(CONFIG_FILE, config)
                invalidate_config()
            await ctx.send(f"drama on {channel.mention} now on.")
        else:
            await ctx.send("Usage: `.confess set #channel`")

    # ── alias ──────────────────────────────────────────────────
    @commands.command(name="alias")
    @help_meta(usage=".alias · .alias <new> <existing> · .alias remove <name>", desc="list / add / remove custom command aliases.", section="Server Management", admin=True)
    async def alias(self, ctx, *args: str):
        """List, add, or remove custom command aliases."""
        # ── show list (anyone) ────────────────────────────────
        if not args:
            return await self._show_alias_list(ctx)

        # ── modifications: admin only ─────────────────────────
        if not is_owner_or_creator(ctx) and not ctx.author.guild_permissions.administrator:
            return await ctx.message.add_reaction("<:redlotus:1263556248310386800>")

        action = args[0].lower()

        # remove
        if action in ("remove", "rm", "del", "delete"):
            if len(args) < 2:
                return await ctx.send("-# `.alias remove <name>`")
            name = args[1].lower().lstrip(".")
            async with self._aliases_lock:
                data = load_json(ALIASES_FILE) or {}
                if name not in data:
                    return await ctx.send(f"-# `{name}` isn't a custom alias")
                del data[name]
                save_json(ALIASES_FILE, data)
                invalidate_aliases()
            return await ctx.message.add_reaction("<:redlotus:1263556248310386800>")

        # add — accepts both `.alias add <new> <target>` and `.alias <new> <target>`
        if action == "add" and len(args) >= 3:
            new_name, target = args[1], args[2]
        elif len(args) >= 2:
            new_name, target = args[0], args[1]
        else:
            return await ctx.send(
                "-# usage: `.alias <new> <existing>` or `.alias remove <name>`"
            )

        new_name = new_name.lower().lstrip(".")
        target = target.lower().lstrip(".")

        if not self.bot.get_command(target):
            return await ctx.send(f"-# `{target}` isn't a real command")

        # don't shadow a real command or built-in alias
        if self.bot.get_command(new_name):
            return await ctx.send(
                f"-# `{new_name}` is already a command (or built-in alias)"
            )

        async with self._aliases_lock:
            data = load_json(ALIASES_FILE) or {}
            data[new_name] = target
            save_json(ALIASES_FILE, data)
            invalidate_aliases()
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    async def _show_alias_list(self, ctx):
        # built-in
        builtin_lines = []
        for cmd in sorted(self.bot.commands, key=lambda c: c.name):
            if cmd.aliases:
                aka = ", ".join(f"`.{a}`" for a in cmd.aliases)
                builtin_lines.append(f"`.{cmd.name}` → {aka}")

        # custom (always read fresh so list reflects latest add/remove)
        custom = load_json(ALIASES_FILE) or {}
        custom_lines = [
            f"`.{a}` → `.{t}`" for a, t in sorted(custom.items())
        ]

        color = get_embed_color(ctx.guild.id) if ctx.guild else 0xFF0000
        embed = discord.Embed(title="Aliases", color=color)
        if builtin_lines:
            embed.add_field(
                name="Built-in",
                value="\n".join(builtin_lines[:25]) or "—",
                inline=False,
            )
        if custom_lines:
            embed.add_field(
                name="Custom",
                value="\n".join(custom_lines[:25]) or "—",
                inline=False,
            )
        if not builtin_lines and not custom_lines:
            embed.description = "no aliases yet — add one with `.alias <new> <existing>`"
        await ctx.send(embed=embed)

    # ── purge ─────────────────────────────────────────────────
    @commands.group(name="purge", invoke_without_command=True)
    @help_meta(usage=".purge · .purge bots [limit]", desc="root — shows subcommands. .purge bots bulk-deletes bot and prefix messages.", section="Moderation")
    async def purge_group(self, ctx):
        await ctx.send("-# subcommands: `bots` — `.purge bots`")

    # ── cmd channel rules ──────────────────────────────────────────
    @help_meta(usage=".cmd <allow|deny|clear|show>", desc="manage command channel rules — restrict or block commands in specific channels.", section="Command Channels")
    @commands.group(name="cmd", invoke_without_command=True)
    async def cmd_group(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not getattr(ctx.author.guild_permissions, "administrator", False):
            return await ctx.send("-# administrator only")

        await ctx.send(
            "-# usage:\n"
            "`.cmd allow <#ch>... <category|command>`\n"
            "`.cmd deny  <#ch>... <category|command>`\n"
            "`.cmd clear <category|command>`\n"
            "`.cmd show [category|command]`"
        )

    def _require_admin(self, ctx: commands.Context) -> bool:
        return bool(ctx.guild and getattr(ctx.author.guild_permissions, "administrator", False))

    def _parse_channel_ids(self, parts: list[str]) -> list[int]:
        ids: list[int] = []
        for p in parts:
            s = (p or "").strip()
            if s.startswith("<#") and s.endswith(">"):
                s = s[2:-1]
            try:
                ids.append(int(s))
            except ValueError:
                continue
        return ids

    @cmd_group.command(name="allow")
    @help_meta(usage=".cmd allow <#channel>... <category|command>", desc="restrict a category/command to specific channels.", section="Command Channels")
    async def cmd_allow(self, ctx: commands.Context, *args: str):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._require_admin(ctx):
            return await ctx.send("-# administrator only")

        if len(args) < 2:
            return await ctx.send("-# usage: `.cmd allow <#channel>... <category|command>`")

        target = args[-1].strip().lower()
        channel_parts = list(args[:-1])
        channel_ids = self._parse_channel_ids(channel_parts)

        if not channel_ids:
            return await ctx.send("-# couldn't resolve any channels. use mentions or ids.")

        set_cmd_channel_rule(ctx.guild.id, target, "allow", channel_ids)
        await ctx.send(f"-# allowed `{target}` only in {len(channel_ids)} channel(s).")

    @cmd_group.command(name="deny")
    @help_meta(usage=".cmd deny <#channel>... <category|command>", desc="block a category/command in specific channels.", section="Command Channels")
    async def cmd_deny(self, ctx: commands.Context, *args: str):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._require_admin(ctx):
            return await ctx.send("-# administrator only")

        if len(args) < 2:
            return await ctx.send("-# usage: `.cmd deny <#channel>... <category|command>`")

        target = args[-1].strip().lower()
        channel_parts = list(args[:-1])
        channel_ids = self._parse_channel_ids(channel_parts)

        if not channel_ids:
            return await ctx.send("-# couldn't resolve any channels. use mentions or ids.")

        set_cmd_channel_rule(ctx.guild.id, target, "deny", channel_ids)
        await ctx.send(f"-# denied `{target}` in {len(channel_ids)} channel(s).")

    @cmd_group.command(name="clear")
    @help_meta(usage=".cmd clear <category|command>", desc="remove rule for a category/command (works everywhere).", section="Command Channels")
    async def cmd_clear(self, ctx: commands.Context, *args: str):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._require_admin(ctx):
            return await ctx.send("-# administrator only")

        if not args:
            return await ctx.send("-# usage: `.cmd clear <category|command>`")

        # target can contain spaces; rebuild from all args
        target = " ".join(a.strip() for a in args if a.strip()).lower()
        clear_cmd_channel_rule(ctx.guild.id, target)
        await ctx.send(f"-# cleared rule for `{target}`.")

    @cmd_group.command(name="show")
    @help_meta(usage=".cmd show [category|command]", desc="show active rule(s) for this server.", section="Command Channels")
    async def cmd_show(self, ctx: commands.Context, *args: str):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._require_admin(ctx):
            return await ctx.send("-# administrator only")

        if args:
            target = " ".join(a.strip() for a in args if a.strip()).lower()
            rule = get_cmd_channel_rule(ctx.guild.id, target)
            if not rule:
                return await ctx.send(f"-# no rule for `{target}`.")
            return await ctx.send(
                f"-# rule for `{target}`: mode=`{rule.get('mode')}` channels={len(rule.get('channels') or [])}"
            )

        # show all targets for this guild
        targets = get_cmd_channel_rules().get(str(ctx.guild.id), {}).get("targets", {}) or {}
        if not targets:
            return await ctx.send("-# no command/channel rules set for this server.")

        lines = []
        for k, v in targets.items():
            lines.append(f"`{k}`: {v.get('mode')} · {len(v.get('channels') or [])} channel(s)")
        await ctx.send("-# active rules:\n" + "\n".join(lines))

    @purge_group.command(name="bots")
    @help_meta(usage=".purge bots [limit]", desc="bulk-deletes bot messages and messages starting with `.` in this channel.", section="Moderation")
    async def purge_bots(self, ctx, limit: int = 50):
        if not is_owner_or_creator(ctx) and not ctx.author.guild_permissions.manage_messages:
            return await ctx.send("-# need `manage_messages` perm")

        # Fast predicate: only delete bot messages or messages that *start* with '.'
        # (guard against empty content to avoid extra work / errors)
        def _check(m: discord.Message) -> bool:
            if m.author.bot:
                return True
            c = m.content or ""
            return bool(c) and c[0] == "."

        purge_limit = min(limit, 200)

        # Try bulk deletion for speed (discord.py versions vary).
        try:
            deleted = await ctx.channel.purge(
                limit=purge_limit,
                check=_check,
                bulk=True,
            )
        except TypeError:
            deleted = await ctx.channel.purge(
                limit=purge_limit,
                check=_check,
            )

        await ctx.send(f"-# purged {len(deleted)} messages", delete_after=5)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))

