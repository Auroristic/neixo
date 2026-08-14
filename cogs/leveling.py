import asyncio
import logging

import discord
from discord.ext import commands

from utils import (
    add_xp,
    get_all_level_roles,
    get_embed_color,
    get_leaderboard,
    get_level_role,
    get_user_xp,
    help_meta,
    is_creator,
    is_owner_or_creator,
    set_level_role,
)

logger = logging.getLogger(__name__)

COG_META = {
    "category": "leveling",
    "label": "Leveling",
    "desc": "XP, levels, and leaderboard system.",
}


class Leveling(commands.Cog):
    """XP, leveling, and leaderboard system."""

    def __init__(self, bot):
        self.bot = bot
        self.xp_cooldowns = {}  # Simple cooldown tracking
        self._leveling_disabled = True  # Disabled on startup
        self._backfill_task: asyncio.Task | None = None


    async def cog_load(self):
        """Initialize level-up notification settings and backfill level roles."""
        from utils import _db
        with _db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS leveling_settings (
                    guild_id TEXT PRIMARY KEY,
                    notifications_enabled INTEGER DEFAULT 1,
                    disabled INTEGER DEFAULT 0
                )
            """)
            # Pre-existing tables (created before the disabled column existed)
            # won't get it from CREATE IF NOT EXISTS — migrate in place
            cols = [r[1] for r in conn.execute("PRAGMA table_info(leveling_settings)").fetchall()]
            if "disabled" not in cols:
                conn.execute("ALTER TABLE leveling_settings ADD COLUMN disabled INTEGER DEFAULT 0")
                conn.commit()
            # Persist the disabled flag across restarts (was hardcoded True).
            row = conn.execute(
                "SELECT disabled FROM leveling_settings WHERE guild_id = '__global__'"
            ).fetchone()
            self._leveling_disabled = bool(row[0]) if row else True
        # Backfill level roles as a background task after ready instead of
        # blocking startup (large guilds would stall + spam role edits).
        self._backfill_task = asyncio.create_task(self._backfill_level_roles())

    async def _backfill_level_roles(self) -> None:
        await self.bot.wait_until_ready()
        if self._leveling_disabled:
            return
        for guild in self.bot.guilds:
            try:
                level_roles = get_all_level_roles(guild.id)
                if not level_roles:
                    continue
                for member in guild.members:
                    if member.bot:
                        continue
                    data = get_user_xp(member.id, guild.id)
                    if data:
                        current_level = data["level"]
                        for level, role_id in sorted(level_roles.items()):
                            if current_level >= int(level):
                                try:
                                    role = guild.get_role(int(role_id))
                                    if role and role not in member.roles:
                                        await member.add_roles(role, reason=f"Level role backfill (level {level})")
                                        await asyncio.sleep(0.5)  # avoid 429 spam
                                except Exception as e:
                                    logger.warning(f"Failed to backfill level role: {e}")
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # one guild's failure shouldn't abort backfill for the rest
                logger.warning(f"level role backfill failed for guild {guild.id}: {e}")

    def cog_unload(self) -> None:
        if self._backfill_task and not self._backfill_task.done():
            self._backfill_task.cancel()

    async def cog_check(self, ctx):
        if ctx.guild is None:
            await ctx.send("-# this command only works in servers.")
            return False
        # Allow .disable and .enable commands even when leveling is disabled
        if ctx.command and ctx.command.qualified_name in ("disable", "enable", "disable level", "enable level"):
            return True
        if self._leveling_disabled:
            await ctx.send("-# leveling is disabled. use `.enable level` to turn it on.")
            return False
        return True

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return
        if self._leveling_disabled:
            return


        # Simple cooldown: 1 XP per minute per user
        import time
        now = time.time()
        user_key = f"{message.author.id}:{message.guild.id}"

        if user_key in self.xp_cooldowns:
            if now - self.xp_cooldowns[user_key] < 60:
                return

        self.xp_cooldowns[user_key] = now

        # Periodic cleanup of stale cooldown entries
        if len(self.xp_cooldowns) > 5000:
            cutoff = now - 3600
            stale = [k for k, t in self.xp_cooldowns.items() if t < cutoff]
            for k in stale:
                del self.xp_cooldowns[k]

        # Add XP
        result = add_xp(message.author.id, message.guild.id, xp_amount=10, messages=1)

        if result["leveled_up"]:
            await self.handle_level_up(message, result)

    async def handle_level_up(self, message, result):
        """Handle level up event - give role if configured."""
        guild_id = message.guild.id
        new_level = result["new_level"]

        # Check for level role
        role_id = get_level_role(guild_id, new_level)
        if role_id:
            try:
                role = message.guild.get_role(int(role_id))
                if role and role not in message.author.roles:
                    await message.author.add_roles(role, reason=f"Level {new_level} reached")
            except Exception as e:
                logger.warning(f"Failed to give level role: {e}")

        # Check if notifications are enabled for this guild
        from utils import _db
        with _db() as conn:
            cursor = conn.execute(
                "SELECT notifications_enabled FROM leveling_settings WHERE guild_id = ?",
                (str(guild_id),)
            )
            row = cursor.fetchone()
            notifications_enabled = row[0] if row else True

        if notifications_enabled:
            # Send small level up message
            embed = discord.Embed(
                title="🎉 Level Up!",
                description=f"{message.author.mention} → **Level {new_level}**",
                color=discord.Color(get_embed_color(guild_id))
            )
            embed.set_thumbnail(url=message.author.display_avatar.url)
            await message.channel.send(embed=embed, delete_after=5)

    @commands.command(aliases=["lvl"])
    @help_meta(
        section="Leveling",
        usage="`.rank [@user]`",
        desc="Checks your or another user's XP rank and level.",
        examples=[".rank", ".rank @user"],
        params=[
            {"name": "user", "type": "discord.Member", "required": False, "desc": "The member to check. Defaults to yourself."},
        ],
        note="Shows current level, XP progress, and server rank.",
    )
    async def rank(self, ctx, member: discord.Member = None):
        """Check your or another user's rank and XP."""
        member = member or ctx.author
        data = get_user_xp(member.id, ctx.guild.id)

        if not data:
            embed = discord.Embed(
                title=f"{member.display_name}'s Rank",
                description="No XP data yet. Start chatting to earn XP!",
                color=discord.Color(get_embed_color(ctx.guild.id))
            )
            await ctx.send(embed=embed)
            return

        # Calculate progress to next level
        current_level = data["level"]
        current_xp = data["xp"]
        xp_for_current = current_level ** 2 * 100
        xp_for_next = (current_level + 1) ** 2 * 100
        progress = ((current_xp - xp_for_current) / (xp_for_next - xp_for_current)) * 100

        embed = discord.Embed(
            title=f"{member.display_name}'s Rank",
            color=discord.Color(get_embed_color(ctx.guild.id))
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Level", value=f"**{current_level}**", inline=True)
        embed.add_field(name="XP", value=f"**{current_xp:,}**", inline=True)
        embed.add_field(name="Messages", value=f"**{data['messages']:,}**", inline=True)

        # Progress bar
        bar_length = 10
        filled = int(bar_length * progress / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        embed.add_field(
            name=f"Progress to Level {current_level + 1}",
            value=f"{bar} {progress:.1f}%",
            inline=False
        )

        # Get rank position
        leaderboard = get_leaderboard(ctx.guild.id, limit=100)
        rank_pos = next((i + 1 for i, entry in enumerate(leaderboard) if entry["user_id"] == str(member.id)), None)
        if rank_pos:
            suffix = "th" if 11 <= rank_pos <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(rank_pos % 10, "th")
            embed.set_footer(text=f"#{rank_pos}{suffix} on the leaderboard")

        await ctx.send(embed=embed)

    @commands.command(name="levelleaderboard", aliases=["llb", "xpleaderboard"])
    @help_meta(
        section="Leveling",
        usage="`.llb [top]`",
        desc="Shows the server XP leaderboard.",
        examples=[".llb"],
        params=[
            {"name": "top", "type": "int", "required": False, "desc": "How many entries to show. Defaults to 10, capped at 200."},
        ],
        note="Displays the top XP earners in the server.",
    )
    async def levelleaderboard(self, ctx, top: int = 10):
        """Show the server's top users by XP."""
        top = min(top, 200)
        data = get_leaderboard(ctx.guild.id, limit=top)
        if not data:
            await ctx.send("No XP data yet. Start chatting!")
            return

        rows = [(int(e["user_id"]), e["xp"]) for e in data]
        from cogs.serverstats import LBPageView
        view = LBPageView(
            self.bot, ctx, rows,
            title="XP Leaderboard",
            subtitle=f"highest XP in /{ctx.guild.name}",
            unit=" XP",
        )
        async with ctx.typing():
            await view.fetch_assets()
            file = await view.render_file()
            view.message = await ctx.send(file=file, view=view)

    @commands.group(invoke_without_command=True)
    @help_meta(
        section="Leveling",
        usage="`.levelrole <level> <@role>`",
        desc="Assigns a role to be awarded at a specific level.",
        examples=[".levelrole 5 @Bronze", ".levelrole 10 @Silver"],
        params=[
            {"name": "level", "type": "int", "required": True, "desc": "The level at which to award the role."},
            {"name": "role", "type": "discord.Role", "required": True, "desc": "The role to assign."},
        ],
        note="Admin only. The role is automatically assigned when a member reaches the specified level. Run with no args to list configured level roles.",
        admin=True,
    )
    async def levelrole(self, ctx, level: int = None, role: discord.Role = None):
        """Manage level-up roles. Use `.levelrole 5 @Role` to set a role for level 5."""
        if not is_owner_or_creator(ctx) and not ctx.author.guild_permissions.administrator:
            return await ctx.send("admin only")
        if level is None:
            # Show all level roles
            roles = get_all_level_roles(ctx.guild.id)
            if not roles:
                await ctx.send("No level roles configured. Use `.levelrole 5 @Role` to add one.")
                return

            embed = discord.Embed(
                title="Level Roles",
                description="Roles given when reaching specific levels",
                color=discord.Color(get_embed_color(ctx.guild.id))
            )
            for lvl, role_id in sorted(roles.items()):
                role_obj = ctx.guild.get_role(int(role_id))
                role_name = role_obj.mention if role_obj else f"<@&{role_id}>"
                embed.add_field(name=f"Level {lvl}", value=role_name, inline=True)

            await ctx.send(embed=embed)
        elif role is not None:
            # Set level role
            set_level_role(ctx.guild.id, level, role.id)
            await ctx.send(f"✅ Role {role.mention} will be given at level {level}!")
        else:
            await ctx.send("Usage: `.levelrole 5 @Role` to set, `.levelrole remove 5` to remove")

    @levelrole.command()
    @help_meta(
        section="Leveling",
        usage="`.levelrole remove <level>`",
        desc="Removes a level role configuration.",
        examples=[".levelrole remove 5"],
        params=[
            {"name": "level", "type": "int", "required": True, "desc": "The level to remove the role reward from."},
        ],
        note="Admin only.",
        admin=True,
    )
    async def remove(self, ctx, level: int):
        """Remove a level role."""
        if not is_owner_or_creator(ctx) and not ctx.author.guild_permissions.administrator:
            return await ctx.send("admin only")
        from utils import _db
        with _db() as conn:
            cursor = conn.execute(
                "DELETE FROM level_roles WHERE guild_id = ? AND level = ?",
                (str(ctx.guild.id), level)
            )
            if cursor.rowcount > 0:
                await ctx.send(f"✅ Removed level role for level {level}")
            else:
                await ctx.send(f"No role configured for level {level}")

    @commands.group(invoke_without_command=True)
    @help_meta(
        section="Leveling",
        usage="`.levelnotify [enable|disable]`",
        desc="Toggles or checks level-up notification status for this server.",
        examples=[".levelnotify", ".levelnotify enable", ".levelnotify disable"],
        params=[
            {"name": "action", "type": "str", "required": False, "desc": "`enable` or `disable`. Omit to check current status."},
        ],
        note="Users still gain XP and roles even when notifications are disabled.",
    )
    async def levelnotify(self, ctx, action: str = None):
        """Toggle level-up notifications for this server.
        
        Usage:
        `.levelnotify enable` - Enable notifications
        `.levelnotify disable` - Disable notifications
        `.levelnotify` - Check current status
        """
        from utils import _db

        if action is None:
            # Check current status
            with _db() as conn:
                cursor = conn.execute(
                    "SELECT notifications_enabled FROM leveling_settings WHERE guild_id = ?",
                    (str(ctx.guild.id),)
                )
                row = cursor.fetchone()
                enabled = row[0] if row else True

            status = "✅ Enabled" if enabled else "❌ Disabled"
            embed = discord.Embed(
                title="Level-Up Notifications",
                description=f"Current status: **{status}**",
                color=discord.Color(get_embed_color(ctx.guild.id))
            )
            embed.add_field(
                name="How to change",
                value="Use `.levelnotify enable` or `.levelnotify disable`",
                inline=False
            )
            await ctx.send(embed=embed)
        elif action.lower() == "enable":
            with _db() as conn:
                conn.execute("""
                    INSERT INTO leveling_settings (guild_id, notifications_enabled) 
                    VALUES (?, 1)
                    ON CONFLICT(guild_id) DO UPDATE SET notifications_enabled = 1
                """, (str(ctx.guild.id),))
            await ctx.send("✅ Level-up notifications **enabled**!")
        elif action.lower() == "disable":
            with _db() as conn:
                conn.execute("""
                    INSERT INTO leveling_settings (guild_id, notifications_enabled) 
                    VALUES (?, 0)
                    ON CONFLICT(guild_id) DO UPDATE SET notifications_enabled = 0
                """, (str(ctx.guild.id),))
            await ctx.send("❌ Level-up notifications **disabled**! Users will still gain XP and roles, but no messages will be sent.")
        else:
            await ctx.send("Invalid action. Use `.levelnotify enable` or `.levelnotify disable`.")

    @levelnotify.command()
    @help_meta(
        section="Leveling",
        usage="`.levelnotify disable`",
        desc="Disables level-up notifications for this server.",
        examples=[".levelnotify disable"],
        params=[],
        note="Users will still gain XP and roles, but no level-up messages are sent.",
    )
    async def disable(self, ctx):
        """Disable level-up notifications."""
        from utils import _db
        with _db() as conn:
            conn.execute("""
                INSERT INTO leveling_settings (guild_id, notifications_enabled) 
                VALUES (?, 0)
                ON CONFLICT(guild_id) DO UPDATE SET notifications_enabled = 0
            """, (str(ctx.guild.id),))
        await ctx.send("❌ Level-up notifications **disabled**!")

    @levelnotify.command()
    @help_meta(
        section="Leveling",
        usage="`.levelnotify enable`",
        desc="Enables level-up notifications for this server.",
        examples=[".levelnotify enable"],
        params=[],
        note="A level-up message is sent in the channel when someone levels up.",
    )
    async def enable(self, ctx):
        """Enable level-up notifications."""
        from utils import _db
        with _db() as conn:
            conn.execute("""
                INSERT INTO leveling_settings (guild_id, notifications_enabled) 
                VALUES (?, 1)
                ON CONFLICT(guild_id) DO UPDATE SET notifications_enabled = 1
            """, (str(ctx.guild.id),))
        await ctx.send("✅ Level-up notifications **enabled**!")

    @commands.command(hidden=True)
    @help_meta(
        section="Leveling",
        usage="`.givexp <amount> [@user]`",
        desc="Manually gives XP to a user (admin only).",
        examples=[".givexp 100", ".givexp 500 @user"],
        params=[
            {"name": "amount", "type": "int", "required": True, "desc": "Amount of XP to give."},
            {"name": "user", "type": "discord.Member", "required": False, "desc": "The target member. Defaults to yourself."},
        ],
        note="Admin only. Hidden from help.",
        admin=True,
    )
    async def givexp(self, ctx, xp: int, user: discord.Member = None):
        """Admin command to give XP (hidden)."""
        if not (ctx.author.id == ctx.guild.owner_id or is_creator(ctx.author.id)):
            return

        user = user or ctx.author
        xp = max(xp, 0)
        result = add_xp(user.id, ctx.guild.id, xp_amount=xp, messages=0)

        if result["leveled_up"]:
            await ctx.send(f"✅ Gave {xp} XP to {user.mention}. They leveled up to {result['new_level']}!")
        else:
            await ctx.send(f"✅ Gave {xp} XP to {user.mention}. They now have {result['xp']} XP.")

    @commands.command(name="disable")
    @help_meta(
        section="Leveling",
        usage="`.disable <command>`  ·  `.disable`",
        desc="Disables a command for this server (owner/creator/whitelist only).",
        examples=[".disable rtop", ".disable welcome test"],
        params=[
            {"name": "command", "type": "str", "required": False,
             "desc": "Command to disable (subcommands like `welcome test` work). Omit to list disabled commands. `level` disables the leveling system."},
        ],
        note=("guild owner, creator, or whitelisted users only. `.enable <command>` turns it back on. "
              "`.disable level` is creator-only."),
    )
    async def disable_cmd(self, ctx, *, command: str = None):
        """Disable a command for this guild, or the leveling system."""
        # 1. level = the existing bot-global leveling system flag — CREATOR-ONLY.
        #    Pre-existing behavior (leveling.py today checks only is_creator);
        #    the flag is bot-global so only the creator toggles it. Deliberately
        #    differs from the per-guild path's owner/creator/whitelist permission.
        command = command.lower().strip() if command else None
        if command == "level":
            if not is_creator(ctx.author.id):
                return await ctx.send("creator only")
            self._leveling_disabled = True
            from utils import _db
            with _db() as conn:
                conn.execute(
                    "INSERT INTO leveling_settings (guild_id, notifications_enabled, disabled) "
                    "VALUES ('__global__', 1, 1) "
                    "ON CONFLICT(guild_id) DO UPDATE SET disabled = 1"
                )
            return await ctx.send("❌ Leveling system **disabled**. Use `.enable level` to re-enable.")

        # 2. generic per-guild command disable
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if not await self._can_disable(ctx):
            return await ctx.send("-# no perms")
        if command is None:
            return await ctx.send(self._disabled_list_text(ctx))
        cmd_name = command.lower().lstrip(".")
        from utils import get_aliases
        cmd_name = get_aliases().get(cmd_name, cmd_name)
        if cmd_name in ("disable", "enable", "help"):
            return await ctx.send("-# can't disable that")
        cmd = self.bot.get_command(cmd_name)
        if cmd is None:
            return await ctx.send("-# no command called that")
        cmd_name = cmd.qualified_name.lower()
        from utils import add_disabled_command
        add_disabled_command(ctx.guild.id, cmd_name)
        await ctx.message.add_reaction("<:redlotus:1263556248310386800>")

    @commands.command(name="enable")
    @help_meta(
        section="Leveling",
        usage="`.enable <command>`  ·  `.enable`",
        desc="Re-enables a disabled command for this server (owner/creator/whitelist only).",
        examples=[".enable rtop"],
        params=[
            {"name": "command", "type": "str", "required": False,
             "desc": "Command to re-enable. Omit to list disabled commands. `level` re-enables the leveling system."},
        ],
        note="guild owner, creator, or whitelisted users only.",
    )
    async def enable_cmd(self, ctx, *, command: str = None):
        """Re-enable a command for this guild, or the leveling system."""
        # 1. level = the existing bot-global leveling system flag — CREATOR-ONLY.
        #    Pre-existing behavior (leveling.py today checks only is_creator);
        #    the flag is bot-global so only the creator toggles it. Deliberately
        #    differs from the per-guild path's owner/creator/whitelist permission.
        command = command.lower().strip() if command else None
        if command == "level":
            if not is_creator(ctx.author.id):
                return await ctx.send("creator only")
            self._leveling_disabled = False
            from utils import _db
            with _db() as conn:
                conn.execute(
                    "INSERT INTO leveling_settings (guild_id, notifications_enabled, disabled) "
                    "VALUES ('__global__', 1, 0) "
                    "ON CONFLICT(guild_id) DO UPDATE SET disabled = 0"
                )
            return await ctx.send("✅ Leveling system **enabled**.")

        # 2. generic per-guild command enable
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if not await self._can_disable(ctx):
            return await ctx.send("-# no perms")
        if command is None:
            return await ctx.send(self._disabled_list_text(ctx))
        cmd_name = command.lower().lstrip(".")
        from utils import get_aliases
        cmd_name = get_aliases().get(cmd_name, cmd_name)
        if cmd_name in ("disable", "enable", "help"):
            return await ctx.send("-# can't enable that")
        cmd = self.bot.get_command(cmd_name)
        if cmd is None:
            return await ctx.send("-# no command called that")
        cmd_name = cmd.qualified_name.lower()
        from utils import remove_disabled_command
        remove_disabled_command(ctx.guild.id, cmd_name)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    async def _can_disable(self, ctx) -> bool:
        if is_creator(ctx.author.id):
            return True
        if ctx.author.id == ctx.guild.owner_id:
            return True
        from utils import get_config
        whitelist = (get_config() or {}).get(str(ctx.guild.id), {}).get("whitelist", [])
        return str(ctx.author.id) in {str(uid) for uid in whitelist}

    def _disabled_list_text(self, ctx) -> str:
        from utils import get_disabled_commands
        names = get_disabled_commands(ctx.guild.id)
        if not names:
            return "-# nothing disabled here."
        return "-# disabled here: " + ", ".join(f".{n}" for n in names)


async def setup(bot):
    await bot.add_cog(Leveling(bot))
    logger.info("Loaded cogs.leveling")
