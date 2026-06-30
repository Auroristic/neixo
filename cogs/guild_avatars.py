import logging

import discord
from discord.ext import commands

from utils import (
    get_embed_color,
    get_guild_avatar,
    help_meta,
    is_owner_or_creator,
    remove_guild_avatar,
    set_guild_avatar,
)

logger = logging.getLogger(__name__)

COG_META = {
    "category": "profile",
    "label": "Profile",
    "desc": "Per-guild custom avatars and profiles.",
}


class GuildAvatars(commands.Cog):
    """Per-guild custom avatars and profile pictures."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        if ctx.guild is None:
            await ctx.send("This command only works in servers.")
            return False
        return True

    @commands.command()
    @help_meta(
        section="Profile",
        usage=".setavatar <image_url>",
        desc="Sets a custom avatar for this server only.",
        examples=[".setavatar https://i.imgur.com/abc.png"],
        params=[
            {"name": "image_url", "type": "str", "required": True, "desc": "Direct URL to the image to use as avatar."},
        ],
        note="The avatar only shows in this server. Other servers will still see your global Discord avatar.",
    )
    async def setavatar(self, ctx, *, image_url: str = None):
        if not is_owner_or_creator(ctx):
            return await ctx.send("owner only")
        # Get image URL from attachment or argument
        if ctx.message.attachments:
            image_url = ctx.message.attachments[0].url
        elif not image_url:
            await ctx.send("Please provide an image URL or attach an image!")
            return

        # Validate it's an image
        clean_url = image_url.split('?')[0].split('#')[0]
        if not any(clean_url.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
            await ctx.send("That doesn't look like a valid image URL!")
            return

        # Only accept Discord CDN URLs
        if not image_url.startswith(("https://cdn.discordapp.com/", "https://media.discordapp.net/")):
            await ctx.send("Only Discord CDN URLs are allowed!")
            return

        # Save to database
        set_guild_avatar(ctx.guild.id, ctx.author.id, image_url)
        await ctx.message.add_reaction("<:7079verifiedblacksimplified:1255031445806780467>")

    @commands.command(aliases=["clearavatar"])
    @help_meta(
        section="Profile",
        usage=".removeavatar",
        desc="Removes your custom server avatar.",
        examples=[".removeavatar"],
        params=[],
        note="Your global Discord avatar will be shown again.",
    )
    async def removeavatar(self, ctx):
        """Remove your custom avatar for this server and revert to global avatar."""
        removed = remove_guild_avatar(ctx.guild.id, ctx.author.id)

        if removed:
            await ctx.message.add_reaction("<:7079verifiedblacksimplified:1255031445806780467>")
        else:
            await ctx.send("-# you don't have a custom avatar set for this server")

    @commands.command(aliases=["pfps"])
    @help_meta(
        section="Profile",
        usage=".serveravatars",
        desc="Shows all custom avatars set in this server.",
        examples=[".serveravatars"],
        params=[],
        note="Displays a paginated list of all users with custom server avatars.",
    )
    async def serveravatars(self, ctx):
        """Show all members who have custom avatars in this server."""
        from utils import _db

        with _db() as conn:
            rows = conn.execute(
                "SELECT user_id, avatar_url FROM guild_avatars WHERE guild_id = ?",
                (str(ctx.guild.id),)
            ).fetchall()

        if not rows:
            await ctx.send("No custom avatars set in this server yet!")
            return

        embed = discord.Embed(
            title=f"🖼️ Custom Avatars in {ctx.guild.name}",
            description=f"{len(rows)} member(s) have custom avatars",
            color=discord.Color(get_embed_color(ctx.guild.id))
        )

        # Show up to 10 avatars inline, rest in footer
        for i, (user_id, avatar_url) in enumerate(rows[:10]):
            user = ctx.guild.get_member(int(user_id))
            name = user.display_name if user else f"<@{user_id}>"
            embed.add_field(name=name, value="[Avatar Link](" + avatar_url + ")", inline=True)

        if len(rows) > 10:
            embed.set_footer(text=f"...and {len(rows) - 10} more")

        await ctx.send(embed=embed)

    @commands.command()
    @help_meta(
        section="Profile",
        usage=".profile [@user]",
        desc="Views your or another user's profile with server avatar.",
        examples=[".profile", ".profile @user"],
        params=[
            {"name": "user", "type": "discord.Member", "required": False, "desc": "The member to view. Defaults to yourself."},
        ],
        note="Shows the server-specific profile including custom avatar.",
    )
    async def profile(self, ctx, member: discord.Member = None):
        """View a user's profile with their custom server avatar if set."""
        member = member or ctx.author

        # Check for custom guild avatar
        custom_avatar = get_guild_avatar(ctx.guild.id, member.id)
        display_avatar = custom_avatar if custom_avatar else member.display_avatar.url

        embed = discord.Embed(
            title=f"{member.display_name}'s Profile",
            color=discord.Color(get_embed_color(ctx.guild.id))
        )
        embed.set_thumbnail(url=display_avatar)
        embed.add_field(name="Global Avatar", value="[Link](" + member.display_avatar.url + ")", inline=True)

        if custom_avatar:
            embed.add_field(name="Server Avatar", value="[Link](" + custom_avatar + ")", inline=True)
            embed.set_footer(text="✨ Has custom server avatar")
        else:
            embed.set_footer(text="Using global avatar")

        # Add join date
        embed.add_field(
            name="Member Since",
            value=member.joined_at.strftime("%B %d, %Y") if member.joined_at else "Unknown",
            inline=False
        )

        await ctx.send(embed=embed)

    @commands.command(hidden=True)
    @help_meta(
        section="Guild Avatars",
        usage=".viewavatar [@user]",
        desc="Views a user's custom server avatar in full size.",
        examples=[".viewavatar", ".viewavatar @user"],
        params=[
            {"name": "user", "type": "discord.Member", "required": False, "desc": "The member to view. Defaults to yourself."},
        ],
        note="Shows the full-resolution custom server avatar.",
    )
    async def viewavatar(self, ctx, member: discord.Member = None):
        """Quick view someone's custom avatar in this server."""
        member = member or ctx.author
        custom_avatar = get_guild_avatar(ctx.guild.id, member.id)

        if custom_avatar:
            embed = discord.Embed(
                title=f"{member.display_name}'s Server Avatar",
                color=discord.Color(get_embed_color(ctx.guild.id))
            )
            embed.set_image(url=custom_avatar)
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"{member.display_name} doesn't have a custom avatar in this server.")


async def setup(bot):
    await bot.add_cog(GuildAvatars(bot))
    logger.info("Loaded cogs.guild_avatars")
