import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from utils import (
    ALIASES_FILE,
    BAIT_CONFIG_FILE,
    BAIT_DEFAULT_DELAY_SECONDS,
    CONFIG_FILE,
    CREATOR_ID,
    IGNORE_LIST_FILE,
    add_bait_exempt_role,
    add_pending_bait_ban,
    clear_bait_settings,
    clear_cmd_channel_rule,
    forgive_bait_user,
    format_bait_delay,
    get_bait_banned,
    get_bait_failed,
    get_bait_settings,
    get_cmd_channel_rule,
    get_cmd_channel_rules,
    get_config,
    get_embed_color,
    get_ignore_list,
    get_pending_bait_bans,
    help_meta,
    invalidate_aliases,
    invalidate_config,
    invalidate_ignore,
    is_creator,
    is_owner_or_creator,
    load_json,
    mark_bait_banned,
    mark_bait_failed,
    parse_bait_delay,
    remove_bait_exempt_role,
    remove_pending_bait_ban,
    save_json,
    set_bait_jailed_role,
    set_bait_logs_channel,
    set_bait_settings,
    set_cmd_channel_rule,
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
        self._bait_tasks: dict[tuple[int, int], asyncio.Task] = {}

    async def cog_load(self):
        self._bait_restore_task = asyncio.create_task(self._restore_bait_tasks())

    def cog_unload(self):
        for task in self._bait_tasks.values():
            task.cancel()
        restore_task = getattr(self, "_bait_restore_task", None)
        if restore_task:
            restore_task.cancel()

    def _is_bait_manager(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return False
        if is_owner_or_creator(ctx):
            return True
        if getattr(ctx.author.guild_permissions, "administrator", False):
            return True
        whitelist = get_config().get(str(ctx.guild.id), {}).get("whitelist", [])
        return str(ctx.author.id) in {str(uid) for uid in whitelist}

    def _is_bait_exempt_member(self, member: discord.Member, settings: dict) -> bool:
        if member.bot:
            return True
        if member.guild and member.id == member.guild.owner_id:
            return True
        if member.id == getattr(member.guild, "owner_id", None):
            return True
        if member.id == CREATOR_ID:
            return True
        if getattr(member.guild_permissions, "administrator", False):
            return True
        whitelist = get_config().get(str(member.guild.id), {}).get("whitelist", [])
        if str(member.id) in {str(uid) for uid in whitelist}:
            return True
        exempt_roles = {str(rid) for rid in settings.get("exempt_role_ids") or []}
        return any(str(role.id) in exempt_roles for role in getattr(member, "roles", []))

    def _bot_can_manage_role(self, guild: discord.Guild, role: discord.Role) -> bool:
        me = guild.me
        if me is None:
            return False
        return me.guild_permissions.manage_roles and not role.managed and role < me.top_role

    async def _send_bait_log(self, guild: discord.Guild, title: str, lines: list[str]) -> None:
        settings = get_bait_settings(guild.id)
        channel_id = settings.get("logs_channel_id")
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id)) if str(channel_id).isdigit() else None
        if channel is None:
            channel = self.bot.get_channel(int(channel_id)) if str(channel_id).isdigit() else None
        if not channel:
            return
        try:
            embed = discord.Embed(
                title=title,
                description="\n".join(lines)[:4000],
                color=get_embed_color(guild.id),
            )
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    async def _restore_bait_tasks(self) -> None:
        await self.bot.wait_until_ready()
        config = load_json(BAIT_CONFIG_FILE) or {}
        for guild_id, settings in config.items():
            if not isinstance(settings, dict):
                continue
            for entry in (settings.get("pending") or {}).values():
                user_id = entry.get("user_id")
                ban_at = entry.get("ban_at")
                if user_id and ban_at:
                    try:
                        self._schedule_bait_ban(int(guild_id), int(user_id), ban_at)
                    except (ValueError, TypeError):
                        continue

    def _schedule_bait_ban(self, guild_id: int, user_id: int, ban_at: str) -> None:
        key = (guild_id, user_id)
        old_task = self._bait_tasks.pop(key, None)
        if old_task:
            old_task.cancel()
        self._bait_tasks[key] = asyncio.create_task(self._bait_ban_after(guild_id, user_id, ban_at))

    async def _bait_ban_after(self, guild_id: int, user_id: int, ban_at: str) -> None:
        try:
            try:
                ban_time = datetime.fromisoformat(ban_at)
            except ValueError:
                ban_time = datetime.now(timezone.utc)
            if ban_time.tzinfo is None:
                ban_time = ban_time.replace(tzinfo=timezone.utc)
            delay = max(0.0, (ban_time - datetime.now(timezone.utc)).total_seconds())
            if delay:
                await asyncio.sleep(delay)

            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            settings = get_bait_settings(guild_id)
            pending = next(
                (
                    p for p in settings.get("pending", {}).values()
                    if p.get("user_id") == str(user_id)
                ),
                None,
            )
            if not pending:
                return

            # Re-check exemptions — user may have become admin/whitelisted/exempt
            member = guild.get_member(user_id)
            if member and self._is_bait_exempt_member(member, settings):
                remove_pending_bait_ban(guild_id, user_id)
                with suppress(discord.HTTPException):
                    await self._revert_bait_action(guild, pending)
                await self._send_bait_log(
                    guild,
                    "Bait ban cancelled",
                    [f"User ID: `{user_id}`", "Reason: user is now exempt"],
                )
                return

            await guild.ban(discord.Object(id=user_id), reason="Bait channel delayed ban")
            mark_bait_banned(guild_id, user_id, banned_at=datetime.now(timezone.utc).isoformat())
            await self._send_bait_log(guild, "Bait ban completed", [f"User ID: `{user_id}`"])
        except asyncio.CancelledError:
            raise
        except discord.Forbidden:
            mark_bait_failed(
                guild_id, user_id,
                failed_at=datetime.now(timezone.utc).isoformat(),
                reason="missing ban permissions or role hierarchy",
            )
            guild = self.bot.get_guild(guild_id)
            if guild:
                await self._send_bait_log(
                    guild,
                    "Bait ban failed",
                    [f"User ID: `{user_id}`", "Reason: missing ban permissions or role hierarchy"],
                )
        except discord.HTTPException as exc:
            mark_bait_failed(
                guild_id, user_id,
                failed_at=datetime.now(timezone.utc).isoformat(),
                reason=str(exc),
            )
            guild = self.bot.get_guild(guild_id)
            if guild:
                await self._send_bait_log(guild, "Bait ban failed", [f"User ID: `{user_id}`", f"Reason: `{exc}`"])
        finally:
            # only pop if we're still the registered task — a re-schedule
            # may have replaced us under the same key while we were awaiting
            if self._bait_tasks.get((guild_id, user_id)) is asyncio.current_task():
                self._bait_tasks.pop((guild_id, user_id), None)

    async def _apply_bait_action(
        self, member: discord.Member, settings: dict, until: datetime,
    ) -> tuple[str, int | None]:
        action = settings.get("action") or "jail"
        if action == "timeout":
            await member.timeout(until, reason="Bait channel trigger")
            return "timeout applied", None

        role_id = settings.get("jailed_role_id")
        role = member.guild.get_role(int(role_id)) if role_id and str(role_id).isdigit() else None
        if role is None:
            raise RuntimeError("jailed role is not configured")
        await member.add_roles(role, reason="Bait channel trigger")
        return "jailed role added", role.id

    async def _revert_bait_action(self, guild: discord.Guild, entry: dict) -> str:
        member = guild.get_member(int(entry.get("user_id", 0)))
        if member is None:
            return "member not in server"
        if entry.get("action") == "timeout":
            await member.timeout(None, reason="Bait forgiven/cancelled")
            return "timeout removed"
        # Use the role that was actually applied; fall back to current settings for old entries
        role_id = entry.get("applied_role_id") or get_bait_settings(guild.id).get("jailed_role_id")
        role = guild.get_role(int(role_id)) if role_id and str(role_id).isdigit() else None
        if role and role in member.roles:
            await member.remove_roles(role, reason="Bait forgiven/cancelled")
            return "jailed role removed"
        return "no jail role to remove"

    def _bait_status_lines(self, guild: discord.Guild, settings: dict) -> list[str]:
        channel = guild.get_channel(int(settings["channel_id"])) if settings.get("channel_id") else None
        logs = guild.get_channel(int(settings["logs_channel_id"])) if settings.get("logs_channel_id") else None
        jail_role = guild.get_role(int(settings["jailed_role_id"])) if settings.get("jailed_role_id") else None
        exempt_roles = [guild.get_role(int(rid)) for rid in settings.get("exempt_role_ids") or [] if str(rid).isdigit()]
        exempt_text = ", ".join(role.mention for role in exempt_roles if role) or "none"
        return [
            f"bait: {'on' if settings.get('enabled') else 'off'}",
            f"channel: {channel.mention if channel else 'not set'}",
            f"delay: {format_bait_delay(settings.get('delay_seconds'))}",
            f"action: {settings.get('action') or 'jail'}",
            f"jailed role: {jail_role.mention if jail_role else 'not set'}",
            f"logs: {logs.mention if logs else 'not set'}",
            f"exempt roles: {exempt_text}",
            "whitelist exempt: yes",
            f"pending bans: {len(settings.get('pending') or {})}",
            f"bait bans: {len(settings.get('banned') or {})}",
        ]

    def _parse_bait_user_id(self, raw: str) -> int | None:
        value = (raw or "").strip()
        if value.startswith("<@") and value.endswith(">"):
            value = value[2:-1].lstrip("!")
        try:
            return int(value)
        except ValueError:
            return None

    def _format_bait_entry(self, guild: discord.Guild, entry: dict, *, banned: bool = False) -> str:
        uid = entry.get("user_id")
        member = guild.get_member(int(uid)) if str(uid).isdigit() else None
        user_text = member.mention if member else f"`{uid}`"
        when_key = "banned_at" if banned else "ban_at"
        when = entry.get(when_key)
        time_text = "unknown time"
        if when:
            try:
                dt = datetime.fromisoformat(when)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                time_text = discord.utils.format_dt(dt, style="R")
            except ValueError:
                time_text = when
        label = "banned" if banned else "bans"
        return f"{user_text} · {label} {time_text} · action {entry.get('action', 'jail')}"

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return

        settings = get_bait_settings(message.guild.id)
        if not settings.get("enabled"):
            return
        if str(message.channel.id) != str(settings.get("channel_id")):
            return
        if self._is_bait_exempt_member(message.author, settings):
            return

        for entry in get_bait_settings(message.guild.id).get("pending", {}).values():
            if entry.get("user_id") == str(message.author.id):
                with suppress(discord.HTTPException):
                    await message.delete()
                return

        now = datetime.now(timezone.utc)
        delay_seconds = int(settings.get("delay_seconds") or BAIT_DEFAULT_DELAY_SECONDS)
        ban_at = now + timedelta(seconds=delay_seconds)

        deleted = "yes"
        try:
            await message.delete()
        except discord.HTTPException:
            deleted = "no"

        try:
            action_result, applied_role_id = await self._apply_bait_action(message.author, settings, ban_at)
        except Exception as exc:
            await self._send_bait_log(
                message.guild,
                "Bait action failed",
                [f"User: {message.author.mention} / `{message.author.id}`", f"Reason: `{exc}`"],
            )
            return

        add_pending_bait_ban(
            message.guild.id,
            user_id=message.author.id,
            channel_id=message.channel.id,
            message_id=message.id,
            action=settings.get("action") or "jail",
            triggered_at=now.isoformat(),
            ban_at=ban_at.isoformat(),
            applied_role_id=applied_role_id,
        )
        self._schedule_bait_ban(message.guild.id, message.author.id, ban_at.isoformat())
        await self._send_bait_log(
            message.guild,
            "Bait triggered",
            [
                f"User: {message.author.mention} / `{message.author.id}`",
                f"Channel: {message.channel.mention}",
                f"Action: {settings.get('action') or 'jail'} ({action_result})",
                f"Ban: {discord.utils.format_dt(ban_at, style='R')}",
                f"Message deleted: {deleted}",
            ],
        )

    # ── whitelist ──────────────────────────────────────────────
    @commands.command(name="whitelist")
    @help_meta(
        usage=".whitelist @user",
        desc="Toggles a user on or off the staff whitelist.",
        section="Server Management",
        owner=True,
        examples=[".whitelist", ".whitelist @user"],
        params=[
            {"name": "user", "type": "discord.Member", "required": False, "desc": "The member to toggle. Omit to see usage."},
        ],
        note="Only the bot owner/creator can manage the whitelist.",
    )
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

            # Normalize legacy int entries to strings (old code stored str(user.id);
            # readers compare str() so int entries never matched). Dedupes too.
            whitelist_ids = list(dict.fromkeys(str(w) for w in whitelist_ids))
            config[str(ctx.guild.id)]['whitelist'] = whitelist_ids

            uid = str(user.id)
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
    @help_meta(
        usage=".whitelistshow",
        desc="Shows all users currently on the staff whitelist.",
        section="Server Management",
        owner=True,
        examples=[".whitelistshow"],
        params=[],
        note="Only the bot owner/creator can view this.",
    )
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
    @help_meta(
        usage=".setcolor #HEX",
        desc="Changes the embed accent colour for this server.",
        section="Server Management",
        owner=True,
        examples=[".setcolor #FF0000", ".setcolor FF0000"],
        params=[
            {"name": "color", "type": "str", "required": True, "desc": "Hex colour code with or without #."},
        ],
        note="Only the bot owner/creator can change the colour.",
    )
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
        embed = discord.Embed(description="nya?", color=color_int)
        await ctx.send(embed=embed)

    # ── ignore ─────────────────────────────────────────────────
    @commands.command(name="ignore")
    @help_meta(
        usage=".ignore @user",
        desc="Toggles ignoring a user — the bot won't respond to them in AI channels.",
        section="Server Management",
        staff=True,
        examples=[".ignore @user"],
        params=[
            {"name": "user", "type": "discord.Member", "required": True, "desc": "The member to ignore or unignore."},
        ],
        note="Staff only (whitelisted users).",
    )
    async def ignore_user(self, ctx, user: discord.Member = None):
        # the ignore list is GLOBAL (all guilds) — only the creator can manage it
        if not is_creator(ctx.author.id):
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
    @help_meta(
        usage=".ignorelist",
        desc="Shows all users currently ignored by the bot in AI channels.",
        section="Server Management",
        staff=True,
        examples=[".ignorelist"],
        params=[],
        note="Staff only (whitelisted users).",
    )
    async def ignore_list(self, ctx):
        if ctx.guild is None:
            return
        config = get_config()
        guild_config = config.get(str(ctx.guild.id), {})
        whitelist = guild_config.get('whitelist', [])
        if not is_owner_or_creator(ctx) and str(ctx.author.id) not in {str(uid) for uid in whitelist}:
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
    @help_meta(
        usage=".confess set #channel",
        desc="Sets the confession channel for the server.",
        section="Server Management",
        admin=True,
        examples=[".confess set #confessions"],
        params=[
            {"name": "action", "type": "str", "required": False, "desc": "Currently only `set` is supported."},
            {"name": "channel", "type": "discord.TextChannel", "required": False, "desc": "The channel to send confessions to."},
        ],
        note="Admin only.",
    )
    async def confess_prefix(self, ctx, action: str = None, channel: discord.TextChannel = None):
        if action == "set":
            if ctx.guild is None:
                return
            perms = getattr(ctx.author, "guild_permissions", None)
            if not is_owner_or_creator(ctx) and not (perms and perms.administrator):
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
    @help_meta(
        usage=".alias .alias <new> <existing> .alias remove <name>",
        desc="Lists, adds, or removes custom command aliases.",
        section="Server Management",
        admin=True,
        examples=[".alias", ".alias bb .bday", ".alias remove bb"],
        params=[
            {"name": "new", "type": "str", "required": False, "desc": "The new alias name."},
            {"name": "existing", "type": "str", "required": False, "desc": "The existing command to alias."},
            {"name": "action", "type": "str", "required": False, "desc": "Use `remove` to delete an alias."},
        ],
        note="Anyone can view the alias list. Adding/removing requires admin. Built-in aliases are shown automatically.",
    )
    async def alias(self, ctx, *args: str):
        """List, add, or remove custom command aliases."""
        # ── show list (anyone) ────────────────────────────────
        if not args:
            return await self._show_alias_list(ctx)

        # ── modifications: admin only ─────────────────────────
        perms = getattr(ctx.author, "guild_permissions", None)
        if not is_owner_or_creator(ctx) and not (perms and perms.administrator):
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
    @help_meta(
        usage=".purge .purge bots [limit]",
        desc="Root command — shows subcommands. .purge bots bulk-deletes bot and prefix messages.",
        section="Moderation",
        examples=[".purge", ".purge bots 100"],
        params=[],
        note="Subcommand: `bots`.",
    )
    async def purge_group(self, ctx):
        await ctx.send("-# subcommands: `bots` — `.purge bots`")

    # ── bait ──────────────────────────────────────────────────
    @commands.group(name="bait", invoke_without_command=True)
    @help_meta(
        usage=".bait <set|off|status|logs|jailrole|exempt|unexempt|pending|banned|forgive>",
        desc=(
            "Configures a bait channel that deletes messages, jails or timeouts users, "
            "then bans them after the delay."
        ),
        section="Moderation",
        admin=True,
        examples=[".bait set #trap 12h jail", ".bait logs #mod-logs", ".bait pending", ".bait forgive @user appealed"],
        params=[],
        note="Admins, whitelisted members, owner/creator, bots, and exempt roles are never punished.",
    )
    async def bait_group(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._is_bait_manager(ctx):
            return await ctx.send("-# administrator or whitelisted only")
        await ctx.send(
            "-# usage:\n"
            "`.bait set #channel [delay] [jail|timeout]`\n"
            "`.bait off`\n"
            "`.bait status`\n"
            "`.bait pending` / `.bait banned`\n"
            "`.bait forgive @user [reason]`"
        )

    @bait_group.command(name="set")
    @help_meta(
        usage=".bait set #channel [delay] [jail|timeout]",
        desc="Sets the bait channel. Messages are deleted, users are jailed or timed out, then banned after the delay.",
        section="Moderation",
        admin=True,
        examples=[".bait set #trap", ".bait set #trap 12h jail", ".bait set #trap 6h timeout"],
        params=[
            {"name": "channel", "type": "discord.TextChannel", "required": True, "desc": "The bait channel."},
            {"name": "delay", "type": "str", "required": False, "desc": "Delay before ban: 30m, 12h, 1d. Default 12h."},
            {
                "name": "action",
                "type": "str",
                "required": False,
                "desc": "Immediate action: jail or timeout. Default jail.",
            },
        ],
        note="Jail mode requires `.bait jailrole @role` first.",
    )
    async def bait_set(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel = None,
        delay: str = None,
        action: str = "jail",
    ):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._is_bait_manager(ctx):
            return await ctx.send("-# administrator or whitelisted only")
        if channel is None:
            return await ctx.send("-# usage: `.bait set #channel [delay] [jail|timeout]`")

        if delay and delay.lower() in ("jail", "timeout"):
            action = delay
            delay = None
        action = (action or "jail").lower().strip()
        if action not in ("jail", "timeout"):
            return await ctx.send("-# action must be `jail` or `timeout`")
        try:
            delay_seconds = parse_bait_delay(delay)
        except ValueError as exc:
            return await ctx.send(f"-# {exc}")

        me = ctx.guild.me
        if me is None or not me.guild_permissions.ban_members:
            return await ctx.send("-# i need `ban_members` for bait")
        perms = channel.permissions_for(me)
        if not perms.manage_messages:
            return await ctx.send("-# i need `manage_messages` in that channel")
        if action == "timeout" and not me.guild_permissions.moderate_members:
            return await ctx.send("-# i need `moderate_members` for timeout bait")
        if action == "jail":
            settings = get_bait_settings(ctx.guild.id)
            role_id = settings.get("jailed_role_id")
            role = ctx.guild.get_role(int(role_id)) if role_id and str(role_id).isdigit() else None
            if role is None:
                return await ctx.send("-# set jailed role first: `.bait jailrole @role`")
            if not self._bot_can_manage_role(ctx.guild, role):
                return await ctx.send("-# i can't manage that jailed role. move my role above it and give manage_roles")

        set_bait_settings(ctx.guild.id, channel_id=channel.id, delay_seconds=delay_seconds, action=action)
        await ctx.send(f"-# bait set: {channel.mention} · {format_bait_delay(delay_seconds)} · `{action}`")

    @bait_group.command(name="off")
    @help_meta(
        usage=".bait off",
        desc="Disables bait and cancels all pending bait bans.",
        section="Moderation",
        admin=True,
        examples=[".bait off"],
        params=[],
        note="Cancels pending bans and tries to remove the immediate jail/timeout action.",
    )
    async def bait_off(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._is_bait_manager(ctx):
            return await ctx.send("-# administrator or whitelisted only")

        pending = get_pending_bait_bans(ctx.guild.id)
        failed = get_bait_failed(ctx.guild.id)
        reverted = 0
        for entry in pending:
            user_id = int(entry["user_id"])
            task = self._bait_tasks.pop((ctx.guild.id, user_id), None)
            if task:
                task.cancel()
            try:
                await self._revert_bait_action(ctx.guild, entry)
                reverted += 1
            except discord.HTTPException:
                pass
        for entry in failed:
            try:
                await self._revert_bait_action(ctx.guild, entry)
                reverted += 1
            except discord.HTTPException:
                pass
        clear_bait_settings(ctx.guild.id)
        total_cancelled = len(pending) + len(failed)
        await self._send_bait_log(
            ctx.guild,
            "Bait disabled",
            [f"By: {ctx.author.mention}", f"Pending/failed bans cancelled: `{total_cancelled}`"],
        )
        await ctx.send(f"-# bait off — cancelled {total_cancelled} pending/failed bans, reverted {reverted} users")

    @bait_group.command(name="status")
    @help_meta(
        usage=".bait status",
        desc="Shows bait channel configuration, exempt roles, and pending/banned counts.",
        section="Moderation",
        admin=True,
        examples=[".bait status"],
        params=[],
        note="Shows exempt roles only; whitelisted members are always exempt.",
    )
    async def bait_status(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._is_bait_manager(ctx):
            return await ctx.send("-# administrator or whitelisted only")
        await ctx.send("-# " + "\n-# ".join(self._bait_status_lines(ctx.guild, get_bait_settings(ctx.guild.id))))

    @bait_group.command(name="logs")
    @help_meta(
        usage=".bait logs #channel",
        desc="Sets the channel where bait trigger, ban, and forgive events are logged.",
        section="Moderation",
        admin=True,
        examples=[".bait logs #mod-logs"],
        params=[{"name": "channel", "type": "discord.TextChannel", "required": True, "desc": "Log channel."}],
        note="Admin or whitelisted only.",
    )
    async def bait_logs(self, ctx: commands.Context, channel: discord.TextChannel = None):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._is_bait_manager(ctx):
            return await ctx.send("-# administrator or whitelisted only")
        if channel is None:
            return await ctx.send("-# usage: `.bait logs #channel`")
        set_bait_logs_channel(ctx.guild.id, channel.id)
        await ctx.send(f"-# bait logs set to {channel.mention}")

    @bait_group.command(name="jailrole")
    @help_meta(
        usage=".bait jailrole @role",
        desc="Sets the role used by jail-mode bait.",
        section="Moderation",
        admin=True,
        examples=[".bait jailrole @jailed"],
        params=[{"name": "role", "type": "discord.Role", "required": True, "desc": "Role to add when bait triggers."}],
        note="The bot must be able to add and remove this role.",
    )
    async def bait_jailrole(self, ctx: commands.Context, role: discord.Role = None):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._is_bait_manager(ctx):
            return await ctx.send("-# administrator or whitelisted only")
        if role is None:
            return await ctx.send("-# usage: `.bait jailrole @role`")
        if not self._bot_can_manage_role(ctx.guild, role):
            return await ctx.send("-# i can't manage that role. move my role above it and give manage_roles")
        set_bait_jailed_role(ctx.guild.id, role.id)
        await ctx.send(f"-# bait jailed role set to {role.mention}")

    @bait_group.command(name="exempt")
    @help_meta(
        usage=".bait exempt @role",
        desc="Adds a role that bait will ignore.",
        section="Moderation",
        admin=True,
        examples=[".bait exempt @staff"],
        params=[{"name": "role", "type": "discord.Role", "required": True, "desc": "Role to exempt."}],
        note="Owner/creator, admins, bots, and whitelisted members are already exempt.",
    )
    async def bait_exempt(self, ctx: commands.Context, role: discord.Role = None):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._is_bait_manager(ctx):
            return await ctx.send("-# administrator or whitelisted only")
        if role is None:
            return await ctx.send("-# usage: `.bait exempt @role`")
        add_bait_exempt_role(ctx.guild.id, role.id)
        await ctx.send(f"-# bait will ignore {role.mention}")

    @bait_group.command(name="unexempt")
    @help_meta(
        usage=".bait unexempt @role",
        desc="Removes a bait-exempt role.",
        section="Moderation",
        admin=True,
        examples=[".bait unexempt @staff"],
        params=[
            {
                "name": "role",
                "type": "discord.Role",
                "required": True,
                "desc": "Role to remove from bait exemptions.",
            }
        ],
        note="Does not affect the existing whitelist exemption.",
    )
    async def bait_unexempt(self, ctx: commands.Context, role: discord.Role = None):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._is_bait_manager(ctx):
            return await ctx.send("-# administrator or whitelisted only")
        if role is None:
            return await ctx.send("-# usage: `.bait unexempt @role`")
        removed = remove_bait_exempt_role(ctx.guild.id, role.id)
        await ctx.send(f"-# {'removed' if removed else 'not exempt'}: {role.mention}")

    @bait_group.command(name="pending")
    @help_meta(
        usage=".bait pending",
        desc="Lists users waiting for delayed bait bans.",
        section="Moderation",
        admin=True,
        examples=[".bait pending"],
        params=[],
        note="Use `.bait forgive @user` to cancel a pending bait ban.",
    )
    async def bait_pending(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._is_bait_manager(ctx):
            return await ctx.send("-# administrator or whitelisted only")
        pending = get_pending_bait_bans(ctx.guild.id)
        if not pending:
            return await ctx.send("-# pending bans: none")
        lines = ["pending bans:"] + [self._format_bait_entry(ctx.guild, entry) for entry in pending[:20]]
        await ctx.send("-# " + "\n-# ".join(lines))

    @bait_group.command(name="banned")
    @help_meta(
        usage=".bait banned",
        desc="Lists users banned by the bait system.",
        section="Moderation",
        admin=True,
        examples=[".bait banned"],
        params=[],
        note="Use `.bait forgive @user` to unban a bait-banned user.",
    )
    async def bait_banned(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._is_bait_manager(ctx):
            return await ctx.send("-# administrator or whitelisted only")
        banned = get_bait_banned(ctx.guild.id)
        if not banned:
            return await ctx.send("-# bait bans: none")
        lines = ["bait bans:"] + [self._format_bait_entry(ctx.guild, entry, banned=True) for entry in banned[:20]]
        await ctx.send("-# " + "\n-# ".join(lines))

    @bait_group.command(name="forgive")
    @help_meta(
        usage=".bait forgive @user|user_id [reason]",
        desc="Cancels a pending bait ban or unbans a user already banned by bait.",
        section="Moderation",
        admin=True,
        examples=[".bait forgive @user appealed", ".bait forgive 123456789012345678 verified human"],
        params=[
            {"name": "user", "type": "discord.Member|int", "required": True, "desc": "Pending or banned bait user."},
            {"name": "reason", "type": "str", "required": False, "desc": "Appeal/forgive reason."},
        ],
        note="Works for pending bait users and users in `.bait banned`.",
    )
    async def bait_forgive(self, ctx: commands.Context, user: str = None, *, reason: str = "no reason provided"):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._is_bait_manager(ctx):
            return await ctx.send("-# administrator or whitelisted only")
        user_id = self._parse_bait_user_id(user or "")
        if user_id is None:
            return await ctx.send("-# usage: `.bait forgive @user|user_id [reason]`")

        settings = get_bait_settings(ctx.guild.id)
        pending_entry = (settings.get("pending") or {}).get(str(user_id))
        banned_entry = (settings.get("banned") or {}).get(str(user_id))
        failed_entry = (settings.get("failed") or {}).get(str(user_id))
        if not pending_entry and not banned_entry and not failed_entry:
            return await ctx.send("-# that user is not in pending bans, bait bans, or failed bans")

        task = self._bait_tasks.pop((ctx.guild.id, user_id), None)
        if task:
            task.cancel()

        action_result = "none"
        if pending_entry or failed_entry:
            # Pending and failed both need action reverted (jail role / timeout)
            entry = forgive_bait_user(ctx.guild.id, user_id)
            try:
                action_result = await self._revert_bait_action(ctx.guild, entry)
            except discord.HTTPException as exc:
                action_result = f"revert failed: {exc}"
        else:
            # Banned — need to unban from Discord first, only remove from records on success
            try:
                await ctx.guild.unban(discord.Object(id=user_id), reason=f"Bait forgive by {ctx.author} - {reason}")
                action_result = "unbanned"
            except discord.NotFound:
                action_result = "not currently banned"
            except discord.HTTPException as exc:
                return await ctx.send(f"-# unban failed, keeping user in bait bans: `{exc}`")
            forgive_bait_user(ctx.guild.id, user_id)

        await self._send_bait_log(
            ctx.guild,
            "Bait forgive",
            [
                f"User ID: `{user_id}`",
                f"Moderator: {ctx.author.mention}",
                f"Reason: {reason}",
                f"Action reverted: {action_result}",
            ],
        )
        await ctx.send(f"-# forgiven `{user_id}` — {action_result}")

    # ── cmd channel rules ──────────────────────────────────────────
    @help_meta(
        usage=".cmd <allow|deny|clear|show>",
        desc="Manages command channel rules — restrict or allow commands in specific channels.",
        section="Command Channels",
        examples=[".cmd allow #mod purge", ".cmd deny #general .ping", ".cmd clear .ping", ".cmd show"],
        params=[],
        note="Admin only. Subcommands: allow, deny, clear, show.",
    )
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
    @help_meta(
        usage=".cmd allow <#channel>... <category|command>",
        desc="Restricts a category or command to only the specified channels.",
        section="Command Channels",
        examples=[".cmd allow #mod #staff purge", ".cmd allow #mod .ping"],
        params=[
            {"name": "channels", "type": "discord.TextChannel", "required": True, "desc": "One or more channel mentions."},
            {"name": "target", "type": "str", "required": True, "desc": "A command name (e.g. `.ping`) or category name."},
        ],
        note="Admin only. Overrides any existing rule for the target.",
    )
    async def cmd_allow(self, ctx: commands.Context, *args: str):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._require_admin(ctx):
            return await ctx.send("-# administrator only")

        if len(args) < 2:
            return await ctx.send("-# usage: `.cmd allow <#channel>... <category|command>`")

        target = args[-1].strip().lower().lstrip(".")
        channel_parts = list(args[:-1])
        channel_ids = self._parse_channel_ids(channel_parts)

        if not channel_ids:
            return await ctx.send("-# couldn't resolve any channels. use mentions or ids.")

        known = getattr(self.bot, "_known_rule_targets", set()) or set()
        if known and target not in known:
            return await ctx.send(f"-# unknown command or category `{target}`.")

        set_cmd_channel_rule(ctx.guild.id, target, "allow", channel_ids)
        await ctx.send(f"-# allowed `{target}` only in {len(channel_ids)} channel(s).")

    @cmd_group.command(name="deny")
    @help_meta(
        usage=".cmd deny <#channel>... <category|command>",
        desc="Blocks a category or command in the specified channels.",
        section="Command Channels",
        examples=[".cmd deny #general .play", ".cmd deny #chat music"],
        params=[
            {"name": "channels", "type": "discord.TextChannel", "required": True, "desc": "One or more channel mentions."},
            {"name": "target", "type": "str", "required": True, "desc": "A command name (e.g. `.play`) or category name."},
        ],
        note="Admin only.",
    )
    async def cmd_deny(self, ctx: commands.Context, *args: str):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._require_admin(ctx):
            return await ctx.send("-# administrator only")

        if len(args) < 2:
            return await ctx.send("-# usage: `.cmd deny <#channel>... <category|command>`")

        target = args[-1].strip().lower().lstrip(".")
        channel_parts = list(args[:-1])
        channel_ids = self._parse_channel_ids(channel_parts)

        if not channel_ids:
            return await ctx.send("-# couldn't resolve any channels. use mentions or ids.")

        known = getattr(self.bot, "_known_rule_targets", set()) or set()
        if known and target not in known:
            return await ctx.send(f"-# unknown command or category `{target}`.")

        set_cmd_channel_rule(ctx.guild.id, target, "deny", channel_ids)
        await ctx.send(f"-# denied `{target}` in {len(channel_ids)} channel(s).")

    @cmd_group.command(name="clear")
    @help_meta(
        usage=".cmd clear <category|command>",
        desc="Removes the channel rule for a category or command so it works everywhere again.",
        section="Command Channels",
        examples=[".cmd clear .ping", ".cmd clear music"],
        params=[
            {"name": "target", "type": "str", "required": True, "desc": "The command or category to clear the rule for."},
        ],
        note="Admin only. The command/category will work in all channels after clearing.",
    )
    async def cmd_clear(self, ctx: commands.Context, *args: str):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._require_admin(ctx):
            return await ctx.send("-# administrator only")

        if not args:
            return await ctx.send("-# usage: `.cmd clear <category|command>`")

        # target can contain spaces; rebuild from all args
        target = " ".join(a.strip() for a in args if a.strip()).lower().lstrip(".")

        known = getattr(self.bot, "_known_rule_targets", set()) or set()
        if known and target not in known:
            return await ctx.send(f"-# unknown command or category `{target}`.")

        clear_cmd_channel_rule(ctx.guild.id, target)
        await ctx.send(f"-# cleared rule for `{target}`.")

    @cmd_group.command(name="show")
    @help_meta(
        usage=".cmd show [category|command]",
        desc="Shows active channel rules for this server, or for a specific target.",
        section="Command Channels",
        examples=[".cmd show", ".cmd show .ping"],
        params=[
            {"name": "target", "type": "str", "required": False, "desc": "Optional command or category to check."},
        ],
        note="Admin only. Run without arguments to see all rules.",
    )
    async def cmd_show(self, ctx: commands.Context, *args: str):
        if ctx.guild is None:
            return await ctx.send("-# this command is guild-only.")
        if not self._require_admin(ctx):
            return await ctx.send("-# administrator only")

        if args:
            target = " ".join(a.strip() for a in args if a.strip()).lower().lstrip(".")
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
    @help_meta(
        usage=".purge bots [limit]",
        desc="Bulk-deletes bot messages and prefix (`.`) messages in this channel.",
        section="Moderation",
        examples=[".purge bots", ".purge bots 100"],
        params=[
            {"name": "limit", "type": "int", "required": False, "desc": "Number of messages to scan (max 200, default 50)."},
        ],
        note="Requires manage_messages permission. Only deletes bot messages and messages starting with `.`.",
    )
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

