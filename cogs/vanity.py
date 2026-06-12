import discord
from discord.ext import commands
import aiosqlite
import os

from utils import help_meta, DATA_DIR

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
        _ALLOWED_COLUMNS = {"substring", "channel_id", "channel_msg", "role_id"}
        for key, value in kwargs.items():
            if key not in _ALLOWED_COLUMNS:
                raise ValueError(f"invalid column: {key}")
            await self.conn.execute(
                f"UPDATE vanity_rules SET {key} = ? WHERE guild_id = ?",
                (value, guild_id),
            )
        await self.conn.commit()

    def format_msg(self, template, member):
        return (
            template
            .replace("{user}", member.mention)
            .replace("{username}", member.name)
            .replace("{server}", member.guild.name)
        )

    @help_meta(
        usage="`.vanity [config|substring|channel|message|role|reset]`",
        desc="Vanity status tracker — root command for all vanity subcommands.",
        staff=True,
        examples=[".vanity", ".vanity config"],
        params=[],
        note="Staff only. Subcommands: config, substring, channel, message, role, reset.",
    )
    @commands.group(name="vanity", invoke_without_command=True)
    async def vanity(self, ctx):
        await ctx.send("**Vanity commands:** `config`, `substring`, `channel`, `message`, `role`, `reset`")

    @help_meta(
        usage=".vanity config",
        desc="Shows the current vanity configuration for this server.",
        section="Vanity",
        staff=True,
        examples=[".vanity config"],
        params=[],
        note="Staff only.",
    )
    @vanity.command(name="config")
    @is_admin()
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

    @help_meta(
        usage=".vanity substring <text>",
        desc="Sets the status substring the bot watches for.",
        section="Vanity",
        staff=True,
        examples=[".vanity substring seoulities", ".vanity substring .gg/"],
        params=[
            {"name": "text", "type": "str", "required": true, "desc": "The substring to match in member statuses."},
        ],
        note="Staff only. Members whose status contains this text will trigger the configured notification.",
    )
    @vanity.command(name="substring")
    @is_admin()
    async def set_substring(self, ctx, *, substring: str):
        await self.ensure_rule(ctx.guild.id)
        await self.update_rule(ctx.guild.id, substring=substring)
        await ctx.send(f"✅ Substring set to `{substring}`")

    @help_meta(
        usage=".vanity channel <#channel>",
        desc="Sets the notification channel for vanity matches.",
        section="Vanity",
        staff=True,
        examples=[".vanity channel #announcements"],
        params=[
            {"name": "channel", "type": "discord.TextChannel", "required": true, "desc": "The channel to send vanity match notifications to."},
        ],
        note="Staff only.",
    )
    @vanity.command(name="channel")
    @is_admin()
    async def set_channel(self, ctx, channel: discord.TextChannel):
        await self.ensure_rule(ctx.guild.id)
        await self.update_rule(ctx.guild.id, channel_id=channel.id)
        await ctx.send(f"✅ Notification channel set to {channel.mention}")

    @help_meta(
        usage=".vanity message <text>",
        desc="Sets the notification message for vanity matches. Use `{{user}}`, `{{username}}`, `{{server}}` as placeholders.",
        section="Vanity",
        staff=True,
        examples=[".vanity message {{user}} is repping {{server}}!"],
        params=[
            {"name": "text", "type": "str", "required": true, "desc": "The message template. Supports `{{user}}`, `{{username}}`, and `{{server}}` placeholders."},
        ],
        note="Staff only.",
    )
    @vanity.command(name="message")
    @is_admin()
    async def set_message(self, ctx, *, message: str):
        await self.ensure_rule(ctx.guild.id)
        await self.update_rule(ctx.guild.id, channel_msg=message)
        await ctx.send(f"✅ Notification message set to `{message}`\n> Placeholders: `{{user}}` `{{username}}` `{{server}}`")

    @help_meta(
        usage=".vanity role <@role>",
        desc="Sets the role to assign when a status match is found.",
        section="Vanity",
        staff=True,
        examples=[".vanity role @Member"],
        params=[
            {"name": "role", "type": "discord.Role", "required": true, "desc": "The role to assign on status match."},
        ],
        note="Staff only.",
    )
    @vanity.command(name="role")
    @is_admin()
    async def set_role(self, ctx, role: discord.Role):
        await self.ensure_rule(ctx.guild.id)
        await self.update_rule(ctx.guild.id, role_id=role.id)
        await ctx.send(f"✅ Role set to {role.mention}")

    @help_meta(
        usage=".vanity reset",
        desc="Resets all vanity configuration for this server.",
        section="Vanity",
        staff=True,
        examples=[".vanity reset"],
        params=[],
        note="Staff only. This cannot be undone.",
    )
    @vanity.command(name="reset")
    @is_admin()
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

        has_vanity = custom_status and substring.lower() in (custom_status.name or "").lower()

        if has_vanity:
            if role_id:
                role = after.guild.get_role(role_id)
                if role and role not in after.roles:
                    try:
                        if after.guild.me.guild_permissions.manage_roles and role < after.guild.me.top_role:
                            await after.add_roles(role, reason="Vanity substring matched.")
                    except discord.Forbidden:
                        pass
            if channel_id and channel_msg and after.id not in self._welcomed:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    self._welcomed.add(after.id)
                    await channel.send(
                        self.format_msg(channel_msg, after),
                        allowed_mentions=discord.AllowedMentions(users=[after])
                    )
        else:
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