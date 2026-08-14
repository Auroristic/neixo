import os

import aiosqlite
import discord
from discord.ext import commands

from utils import DATA_DIR, help_meta

DB_PATH = os.path.join(DATA_DIR, "vanity.db")

# ── cogs/vanity.py ──────────────────────────────────────────────
COG_META = {
    "category": "vanity",
    "label": "Vanity",
    "desc": "Vanity and custom status tools.",
    "staff": True,
}




def is_admin():
    async def predicate(ctx):
        if ctx.guild is None:
            return False
        return ctx.author.guild_permissions.administrator
    return commands.check(predicate)

class Vanity(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.conn = None
        self.db_ready = False
        self._welcomed = set()

    async def cog_load(self):
        self.conn = await aiosqlite.connect(DB_PATH)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS vanity_rules (
                guild_id INTEGER PRIMARY KEY,
                substring TEXT,
                channel_id INTEGER,
                channel_msg TEXT,
                role_id INTEGER
            )
        """)
        await self.conn.commit()
        self.db_ready = True

    async def cog_unload(self):
        if self.conn:
            await self.conn.close()
            self.conn = None
        self.db_ready = False

    async def get_rule(self, guild_id):
        async with self.conn.execute(
            "SELECT substring, channel_id, channel_msg, role_id FROM vanity_rules WHERE guild_id = ?",
            (guild_id,),
        ) as cursor:
            return await cursor.fetchone()

    async def ensure_rule(self, guild_id):
        if not await self.get_rule(guild_id):
            await self.conn.execute(
                "INSERT INTO vanity_rules (guild_id) VALUES (?)", (guild_id,)
            )
            await self.conn.commit()

    async def update_rule(self, guild_id, **kwargs):
        for key, value in kwargs.items():
            if key == "substring":
                query = "UPDATE vanity_rules SET substring = ? WHERE guild_id = ?"
            elif key == "channel_id":
                query = "UPDATE vanity_rules SET channel_id = ? WHERE guild_id = ?"
            elif key == "channel_msg":
                query = "UPDATE vanity_rules SET channel_msg = ? WHERE guild_id = ?"
            elif key == "role_id":
                query = "UPDATE vanity_rules SET role_id = ? WHERE guild_id = ?"
            else:
                raise ValueError(f"invalid column: {key}")
            await self.conn.execute(query, (value, guild_id))
        await self.conn.commit()

    def format_msg(self, template, member):
        return (
            template
            .replace("{{user}}", member.mention)
            .replace("{{username}}", member.name)
            .replace("{{server}}", member.guild.name)
            .replace("{user}", member.mention)
            .replace("{username}", member.name)
            .replace("{server}", member.guild.name)
        )

    @commands.group(name="vanity", invoke_without_command=True)
    @help_meta(
        usage="`.vanity [config|substring|channel|message|role|reset]`",
        desc="Automated vanity URL status tracker — assigns roles and announces when members represent your server.",
        section="Vanity",
        perm_tier="admin",
        discord_perms=["manage_guild", "manage_roles"],
        examples=[".vanity", ".vanity config", ".vanity substring .gg/seoulities"],
        params=[],
        note="Requires Administrator or Manage Server permissions. Subcommands: `config`, `substring`, `channel`, `message`, `role`, `reset`.",
    )
    async def vanity(self, ctx):
        await ctx.send("**Vanity commands:** `config`, `substring`, `channel`, `message`, `role`, `reset`")

    @vanity.command(name="config")
    @is_admin()
    @help_meta(
        usage="`.vanity config`",
        desc="Shows the current vanity URL tracker configuration for this server.",
        section="Vanity",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".vanity config"],
        params=[],
        note="Requires Administrator permission.",
    )
    async def vanity_config(self, ctx):
        await self.ensure_rule(ctx.guild.id)
        rule = await self.get_rule(ctx.guild.id)
        substring, channel_id, channel_msg, role_id = rule
        channel = self.bot.get_channel(channel_id) if channel_id else None
        role = ctx.guild.get_role(role_id) if role_id else None

        embed = discord.Embed(title="Vanity Config", color=0x2b2d31)
        embed.add_field(name="Substring", value=f"`{substring}`" if substring else "Not set", inline=False)
        embed.add_field(name="Notification Channel", value=channel.mention if channel else "Not set", inline=False)
        embed.add_field(name="Notification Message", value=f"`{channel_msg}`" if channel_msg else "Not set", inline=False)
        embed.add_field(name="Role", value=role.mention if role else "Not set", inline=False)
        embed.set_footer(text="Use .vanity <substring|channel|message|role> to configure")
        await ctx.send(embed=embed)

    @vanity.command(name="substring")
    @is_admin()
    @help_meta(
        usage="`.vanity substring <text>`",
        desc="Sets the status text substring the bot watches for in member custom statuses.",
        section="Vanity",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".vanity substring seoulities", ".vanity substring .gg/myguild"],
        params=[
            {"name": "text", "type": "str", "required": True, "desc": "The text snippet to match inside member statuses."},
        ],
        note="Requires Administrator permission. When matched, the vanity role is automatically awarded.",
    )
    async def set_substring(self, ctx, *, substring: str):
        await self.ensure_rule(ctx.guild.id)
        await self.update_rule(ctx.guild.id, substring=substring)
        await ctx.send(f"✅ Substring set to `{substring}`")

    @vanity.command(name="channel")
    @is_admin()
    @help_meta(
        usage="`.vanity channel <#channel>`",
        desc="Sets the announcement channel where vanity status promotions are posted.",
        section="Vanity",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".vanity channel #announcements", ".vanity channel #reps"],
        params=[
            {"name": "channel", "type": "channel", "required": True, "desc": "The channel to send vanity match alerts to."},
        ],
        note="Requires Administrator permission.",
    )
    async def set_channel(self, ctx, channel: discord.TextChannel):
        await self.ensure_rule(ctx.guild.id)
        await self.update_rule(ctx.guild.id, channel_id=channel.id)
        await ctx.send(f"✅ Notification channel set to {channel.mention}")

    @vanity.command(name="message")
    @is_admin()
    @help_meta(
        usage="`.vanity message <text>`",
        desc="Sets the vanity announcement message template. Placeholders: `{{user}}`, `{{username}}`, `{{server}}`.",
        section="Vanity",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".vanity message {{user}} is now repping {{server}}!"],
        params=[
            {"name": "text", "type": "str", "required": True, "desc": "Message template supporting `{{user}}`, `{{username}}`, and `{{server}}` placeholders."},
        ],
        note="Requires Administrator permission.",
    )
    async def set_message(self, ctx, *, message: str):
        await self.ensure_rule(ctx.guild.id)
        await self.update_rule(ctx.guild.id, channel_msg=message)
        await ctx.send(f"✅ Notification message set to `{message}`\n> Placeholders: `{{user}}` `{{username}}` `{{server}}`")

    @vanity.command(name="role")
    @is_admin()
    @help_meta(
        usage="`.vanity role <@role>`",
        desc="Sets the reward role auto-assigned to members who put the vanity in their custom status.",
        section="Vanity",
        perm_tier="admin",
        discord_perms=["manage_roles"],
        examples=[".vanity role @Vanity Rep", ".vanity role @Supporter"],
        params=[
            {"name": "role", "type": "role", "required": True, "desc": "The role to automatically grant."},
        ],
        note="Requires Administrator and Manage Roles permissions. Bot role must be higher than the target role.",
    )
    async def set_role(self, ctx, role: discord.Role):
        await self.ensure_rule(ctx.guild.id)
        await self.update_rule(ctx.guild.id, role_id=role.id)
        await ctx.send(f"✅ Role set to {role.mention}")

    @vanity.command(name="reset")
    @is_admin()
    @help_meta(
        usage="`.vanity reset`",
        desc="Completely resets all vanity tracker settings for this server.",
        section="Vanity",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".vanity reset"],
        params=[],
        note="Requires Administrator permission. This cannot be undone.",
    )
    async def reset_config(self, ctx):
        await self.conn.execute("DELETE FROM vanity_rules WHERE guild_id = ?", (ctx.guild.id,))
        await self.conn.commit()
        await ctx.send("✅ Vanity config reset.")

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if not self.db_ready:
            return

        rule = await self.get_rule(after.guild.id)
        if not rule or not rule[0]:
            return

        substring, channel_id, channel_msg, role_id = rule

        custom_status = next(
            (a for a in after.activities if isinstance(a, discord.CustomActivity)),
            None,
        )

        status_text = f"{custom_status.state or ''} {custom_status.name or ''}".lower() if custom_status else ""
        has_vanity = custom_status is not None and substring.lower() in status_text

        user_key = (after.guild.id, after.id)
        if has_vanity:
            if role_id:
                role = after.guild.get_role(role_id)
                if role and role not in after.roles:
                    try:
                        if after.guild.me.guild_permissions.manage_roles and role < after.guild.me.top_role:
                            await after.add_roles(role, reason="Vanity substring matched.")
                    except discord.Forbidden:
                        pass
            if channel_id and channel_msg and user_key not in self._welcomed:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    try:
                        await channel.send(
                            self.format_msg(channel_msg, after),
                            allowed_mentions=discord.AllowedMentions(users=[after])
                        )
                    except discord.HTTPException:
                        return
                    # mark only after a successful send so a transient
                    # failure doesn't permanently skip this user's welcome
                    self._welcomed.add(user_key)
        else:
            self._welcomed.discard(user_key)
            if role_id:
                role = after.guild.get_role(role_id)
                if role and role in after.roles:
                    try:
                        if after.guild.me.guild_permissions.manage_roles:
                            await after.remove_roles(role, reason="Vanity substring no longer present.")
                    except discord.Forbidden:
                        pass

    @vanity.error
    async def vanity_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ You need administrator permissions to use this.")

async def setup(bot):
    await bot.add_cog(Vanity(bot))
