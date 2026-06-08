import discord
from discord.ext import commands
from discord import app_commands
import logging
from utils import (
    add_xp, add_voice_xp, get_user_xp, get_leaderboard,
    set_level_role, get_level_role, get_all_level_roles,
    get_embed_color, help_meta, get_help_meta
)
import math

logger = logging.getLogger(__name__)

COG_META = {
    "category": "leveling",
    "commands": ["rank", "levelleaderboard", "levelrole", "levelnotify", "givexp"]
}


class Leveling(commands.Cog):
    """XP, leveling, and leaderboard system."""

    def __init__(self, bot):
        self.bot = bot
        self.xp_cooldowns = {}  # Simple cooldown tracking
        self.level_up_notifications = {}  # Guild-specific toggle (default: True)

    async def cog_load(self):
        """Initialize level-up notification settings from DB."""
        from utils import _db
        with _db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS leveling_settings (
                    guild_id TEXT PRIMARY KEY,
                    notifications_enabled INTEGER DEFAULT 1
                )
            """)

    async def cog_check(self, ctx):
        if ctx.guild is None:
            await ctx.send("This command only works in servers.")
            return False
        return True

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return
        
        # Ignore if in ignored channel (check config)
        from utils import get_ignore_list
        ignore_list = get_ignore_list()
        guild_ignores = ignore_list.get(str(message.guild.id), [])
        if str(message.channel.id) in guild_ignores:
            return
        
        # Simple cooldown: 1 XP per minute per user
        import time
        now = time.time()
        user_key = f"{message.author.id}:{message.guild.id}"
        
        if user_key in self.xp_cooldowns:
            if now - self.xp_cooldowns[user_key] < 60:
                return
        
        self.xp_cooldowns[user_key] = now
        
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
                if role and not role in message.author.roles:
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
    @help_meta(section="Leveling", usage=".rank [@user]", desc="Check your or another user's rank")
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

    @help_meta(section="Leveling", usage=".llb", desc="Show server XP leaderboard")
    @commands.command(name="levelleaderboard", aliases=["llb", "xpleaderboard"])
    async def levelleaderboard(self, ctx, top: int = 10):
        """Show the server's top users by XP."""
        top = min(top, 50)  # Max 50
        leaderboard = get_leaderboard(ctx.guild.id, limit=top)
        
        if not leaderboard:
            await ctx.send("No XP data yet. Start chatting!")
            return
        
        embed = discord.Embed(
            title=f"🏆 {ctx.guild.name} Leaderboard",
            color=discord.Color(get_embed_color(ctx.guild.id))
        )
        
        for i, entry in enumerate(leaderboard, 1):
            user_id = int(entry["user_id"])
            user = ctx.guild.get_member(user_id)
            name = user.display_name if user else f"<@{user_id}>"
            suffix = "th" if 11 <= i <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(i % 10, "th")
            
            embed.add_field(
                value=f"**{i}{suffix}** - {name}\nLevel {entry['level']} • {entry['xp']:,} XP",
                inline=False
            )
        
        await ctx.send(embed=embed)

    @commands.group(invoke_without_command=True)
    @help_meta(section="Leveling", usage=".levelrole [level] [@role]", desc="Manage level roles")
    async def levelrole(self, ctx, level: int = None, role: discord.Role = None):
        """Manage level-up roles. Use `.levelrole 5 @Role` to set a role for level 5."""
        if level is None or role is None:
            # Show all level roles
            roles = get_all_level_roles(ctx.guild.id)
            if not roles:
                await ctx.send("No level roles configured. Use `.levelrole <level> <@role>` to add one.")
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
        else:
            # Set level role
            set_level_role(ctx.guild.id, level, role.id)
            await ctx.send(f"✅ Role {role.mention} will be given at level {level}!")

    @levelrole.command()
    @help_meta(section="Leveling", usage=".levelrole remove <level>", desc="Remove a level role")
    async def remove(self, ctx, level: int):
        """Remove a level role."""
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
    @help_meta(section="Leveling", usage=".levelnotify [enable|disable]", desc="Toggle level-up notifications")
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
    @help_meta(section="Leveling", usage=".levelnotify disable", desc="Disable level-up notifications")
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
    @help_meta(section="Leveling", usage=".levelnotify enable", desc="Enable level-up notifications")
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
    @help_meta(section="Leveling", usage=".givexp <amount> [@user]", desc="Give XP to a user (admin only)")
    async def givexp(self, ctx, xp: int, user: discord.Member = None):
        """Admin command to give XP (hidden)."""
        if not (ctx.author.id == ctx.guild.owner_id or ctx.author.id == 887382911924441139):
            return
        
        user = user or ctx.author
        result = add_xp(user.id, ctx.guild.id, xp_amount=xp, messages=0)
        
        if result["leveled_up"]:
            await ctx.send(f"✅ Gave {xp} XP to {user.mention}. They leveled up to {result['new_level']}!")
        else:
            await ctx.send(f"✅ Gave {xp} XP to {user.mention}. They now have {result['xp']} XP.")


async def setup(bot):
    await bot.add_cog(Leveling(bot))
    logger.info("Loaded cogs.leveling")
