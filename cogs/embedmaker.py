"""
cogs/embedmaker.py  —  advanced staff embed builder suite
"""

import io
import json
import logging
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils import help_meta, is_owner_or_creator

log = logging.getLogger(__name__)

COG_META = {
    "category": "theme",
    "label": "Theme",
    "desc": "Custom embed generation, raw JSON builder, and server theme layouts.",
}

_PRESET_COLORS = {
    "dark": 0x121516,
    "black": 0x0A0A0C,
    "white": 0xF0F0F5,
    "red": 0xEF4444,
    "crimson": 0xDC2626,
    "cyan": 0x06B6D4,
    "blue": 0x3B82F6,
    "blurple": 0x5865F2,
    "green": 0x10B981,
    "purple": 0x8B5CF6,
    "pink": 0xEC4899,
    "yellow": 0xEAB308,
    "orange": 0xF97316,
    "gray": 0x71717A,
}


def _parse_color(raw: str) -> int:
    if not raw:
        return 0x121516
    raw_lower = raw.lower().strip().lstrip("#")
    if raw_lower in _PRESET_COLORS:
        return _PRESET_COLORS[raw_lower]
    try:
        return int(raw_lower, 16)
    except ValueError:
        return 0x121516


def _build_embed_from_text(content: str, author_member=None, attachments=None) -> tuple[discord.Embed, str | None]:
    """
    Parses a string with title | description and optional --flags:
      --color <hex|preset>
      --image <url>
      --thumb / --thumbnail <url>
      --footer <text>
      --author <name>
      --author-icon <url>
      --url <link>
      --timestamp
      --field <name> | <value> [--inline]
    """
    color = 0x121516
    image_url = None
    thumb_url = None
    footer_text = f"posted by {author_member.display_name}" if author_member else None
    author_name = None
    author_icon = None
    title_url = None
    add_timestamp = False
    fields = []

    # Check attachments first for image
    if attachments:
        for att in attachments:
            if att.content_type and att.content_type.startswith("image/"):
                image_url = att.url
                break

    # Extract --timestamp flag
    if re.search(r"--timestamp\b", content, re.IGNORECASE):
        add_timestamp = True
        content = re.sub(r"--timestamp\b", "", content, flags=re.IGNORECASE).strip()

    # Extract --color
    m_col = re.search(r"--color\s+([^\s\-]+)", content, re.IGNORECASE)
    if m_col:
        color = _parse_color(m_col.group(1))
        content = (content[:m_col.start()] + content[m_col.end():]).strip()

    # Extract --image
    m_img = re.search(r"--image\s+([^\s]+)", content, re.IGNORECASE)
    if m_img:
        image_url = m_img.group(1).strip()
        content = (content[:m_img.start()] + content[m_img.end():]).strip()

    # Extract --thumb / --thumbnail
    m_thumb = re.search(r"--thumb(?:nail)?\s+([^\s]+)", content, re.IGNORECASE)
    if m_thumb:
        thumb_url = m_thumb.group(1).strip()
        content = (content[:m_thumb.start()] + content[m_thumb.end():]).strip()

    # Extract --author-icon
    m_aicon = re.search(r"--author-icon\s+([^\s]+)", content, re.IGNORECASE)
    if m_aicon:
        author_icon = m_aicon.group(1).strip()
        content = (content[:m_aicon.start()] + content[m_aicon.end():]).strip()

    # Extract --author (surrounded by quotes or up to next flag)
    m_auth = re.search(r'--author\s+(?:"([^"]+)"|([^\-]+?))(?=\s+--|$)', content, re.IGNORECASE)
    if m_auth:
        author_name = (m_auth.group(1) or m_auth.group(2)).strip()
        content = (content[:m_auth.start()] + content[m_auth.end():]).strip()

    # Extract --url
    m_url = re.search(r"--url\s+([^\s]+)", content, re.IGNORECASE)
    if m_url:
        title_url = m_url.group(1).strip()
        content = (content[:m_url.start()] + content[m_url.end():]).strip()

    # Extract --footer (surrounded by quotes or up to next flag)
    m_foot = re.search(r'--footer\s+(?:"([^"]+)"|([^\-]+?))(?=\s+--|$)', content, re.IGNORECASE)
    if m_foot:
        footer_text = (m_foot.group(1) or m_foot.group(2)).strip()
        content = (content[:m_foot.start()] + content[m_foot.end():]).strip()

    # Extract --field entries
    field_matches = re.finditer(r'--field\s+(?:"([^"]+)"|([^\-]+?))(?=\s+--field|\s+--|$)', content, re.IGNORECASE)
    for fm in field_matches:
        f_raw = (fm.group(1) or fm.group(2)).strip()
        is_inline = False
        if "--inline" in f_raw.lower():
            is_inline = True
            f_raw = re.sub(r"--inline\b", "", f_raw, flags=re.IGNORECASE).strip()
        if "|" in f_raw:
            f_name, f_val = f_raw.split("|", 1)
            fields.append((f_name.strip(), f_val.strip(), is_inline))

    content = re.sub(r'--field\s+(?:"([^"]+)"|([^\-]+?))(?=\s+--field|\s+--|$)', '', content, flags=re.IGNORECASE).strip()

    # Parse title | description
    parts = content.split("|", 1)
    title = parts[0].strip()
    desc = parts[1].strip() if len(parts) > 1 else None

    if not title:
        return None, "Title is required before the ` | ` divider."

    embed = discord.Embed(title=title, color=color)
    if desc:
        embed.description = desc
    if title_url:
        embed.url = title_url
    if image_url:
        embed.set_image(url=image_url)
    if thumb_url:
        embed.set_thumbnail(url=thumb_url)
    if footer_text:
        embed.set_footer(text=footer_text)
    if author_name:
        embed.set_author(name=author_name, icon_url=author_icon if author_icon else None)
    if add_timestamp:
        embed.timestamp = datetime.now(timezone.utc)

    for f_name, f_val, f_inline in fields:
        embed.add_field(name=f_name or "\u200b", value=f_val or "\u200b", inline=f_inline)

    return embed, None


class EmbedMaker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _staff(self, ctx) -> bool:
        if ctx.guild is None:
            return False
        if is_owner_or_creator(ctx):
            return True
        perms = getattr(ctx.author, "guild_permissions", None)
        return bool(perms and perms.administrator)

    @commands.group(name="embed", invoke_without_command=True)
    @help_meta(
        usage="`.embed <title> | <description> [--color hex] [--image url] [--thumb url] [--footer text] [--author name] [--url link] [--timestamp]`",
        desc="Creates and posts a sleek custom embed in the current channel with rich formatting flags.",
        section="Theme",
        perm_tier="admin",
        discord_perms=["manage_messages"],
        examples=[
            ".embed Server News | Welcome our new members to the server! --color blurple",
            ".embed Event | Movie night this Friday --color #707080 --thumb https://i.imgur.com/example.png --footer \"Starts at 8 PM UTC\"",
            ".embed Rules | 1. Be polite\n2. No spam --color dark --timestamp",
            ".embed json {\"title\": \"Discohook Export\", \"description\": \"Hello!\", \"color\": 1184278}",
            ".embed edit 1234567890 Updated Title | New description",
            ".embed raw 1234567890",
        ],
        params=[
            {"name": "content", "type": "str", "required": True, "desc": "`<title> | <description>` syntax with optional `--color`, `--image`, `--thumb`, `--footer`, `--author`, `--url`, `--timestamp` flags."},
        ],
        note="Requires Administrator or Manage Messages. Automatically deletes invoking command on success.",
    )
    async def embed(self, ctx: commands.Context, *, content: str = None):
        if not await self._staff(ctx):
            return await ctx.send("-# staff only")
        if not content:
            return await ctx.send(
                "-# usage: `.embed <title> | <description>` — flags: `--color`, `--image`, `--thumb`, `--footer`, `--author`, `--url`, `--timestamp`"
            )

        embed, err = _build_embed_from_text(content, author_member=ctx.author, attachments=ctx.message.attachments)
        if err:
            return await ctx.send(f"-# {err}")

        try:
            await ctx.send(embed=embed)
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
        except discord.HTTPException as e:
            await ctx.send(f"-# couldn't send embed: {str(e).lower()}")

    @embed.command(name="json")
    @help_meta(
        usage="`.embed json <json_payload>`",
        desc="Parses and posts a raw Discord embed JSON payload (supports Discohook JSON).",
        section="Theme",
        perm_tier="admin",
        discord_perms=["manage_messages"],
        examples=[
            '.embed json {"title": "Announcement", "description": "Hello world", "color": 1184278}',
        ],
        params=[
            {"name": "payload", "type": "str", "required": True, "desc": "Valid Discord embed JSON string or attached .json file."},
        ],
        note="Supports raw dict or list containing embeds.",
    )
    async def embed_json(self, ctx: commands.Context, *, payload: str = None):
        if not await self._staff(ctx):
            return await ctx.send("-# staff only")

        # Check attachment if payload is empty
        if not payload and ctx.message.attachments:
            att = ctx.message.attachments[0]
            if att.filename.endswith(".json") or (att.content_type and "json" in att.content_type):
                payload_bytes = await att.read()
                payload = payload_bytes.decode("utf-8")

        if not payload:
            return await ctx.send("-# usage: `.embed json <valid JSON>` or upload a `.json` file")

        clean_json = payload.strip("` \n")
        if clean_json.startswith("json"):
            clean_json = clean_json[4:].strip()

        try:
            data = json.loads(clean_json)
        except Exception as e:
            return await ctx.send(f"-# invalid json: {str(e).lower()}")

        embeds = []
        if isinstance(data, list):
            for item in data[:10]:
                if isinstance(item, dict):
                    embeds.append(discord.Embed.from_dict(item))
        elif isinstance(data, dict):
            if "embeds" in data and isinstance(data["embeds"], list):
                for item in data["embeds"][:10]:
                    embeds.append(discord.Embed.from_dict(item))
            else:
                embeds.append(discord.Embed.from_dict(data))

        if not embeds:
            return await ctx.send("-# no valid embed objects found in json")

        try:
            await ctx.send(embeds=embeds)
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
        except discord.HTTPException as e:
            await ctx.send(f"-# failed to send embed json: {str(e).lower()}")

    @embed.command(name="raw")
    @help_meta(
        usage="`.embed raw <message_id_or_link>`",
        desc="Extracts the raw JSON payload of an existing embed in the channel.",
        section="Theme",
        perm_tier="admin",
        discord_perms=["manage_messages"],
        examples=[
            ".embed raw 123456789012345678",
        ],
        params=[
            {"name": "message", "type": "message", "required": True, "desc": "Message ID or URL containing the embed."},
        ],
    )
    async def embed_raw(self, ctx: commands.Context, message: discord.Message = None):
        if not await self._staff(ctx):
            return await ctx.send("-# staff only")
        if not message:
            return await ctx.send("-# usage: `.embed raw <message_id_or_link>`")

        if not message.embeds:
            return await ctx.send("-# that message does not contain any embeds")

        embed_dict = message.embeds[0].to_dict()
        formatted = json.dumps(embed_dict, indent=2)

        if len(formatted) > 1950:
            file = discord.File(io.StringIO(formatted), filename="embed.json")
            return await ctx.send("-# embed payload is large, attached below:", file=file)

        await ctx.send(f"```json\n{formatted}\n```")

    @embed.command(name="edit")
    @help_meta(
        usage="`.embed edit <message_id> <title> | <description> [flags]`",
        desc="Edits an existing embed previously sent by the bot.",
        section="Theme",
        perm_tier="admin",
        discord_perms=["manage_messages"],
        examples=[
            ".embed edit 1234567890 Updated Title | New description",
        ],
        params=[
            {"name": "message_id", "type": "int", "required": True, "desc": "ID of the bot message to edit."},
            {"name": "content", "type": "str", "required": True, "desc": "New embed content in `<title> | <description>` syntax with flags."},
        ],
    )
    async def embed_edit(self, ctx: commands.Context, message_id: int, *, content: str = None):
        if not await self._staff(ctx):
            return await ctx.send("-# staff only")
        if not content:
            return await ctx.send("-# usage: `.embed edit <message_id> <title> | <description>`")

        try:
            target_msg = await ctx.channel.fetch_message(message_id)
        except discord.NotFound:
            return await ctx.send("-# message not found in this channel")
        except discord.HTTPException as e:
            return await ctx.send(f"-# error fetching message: {str(e).lower()}")

        if target_msg.author.id != self.bot.user.id:
            return await ctx.send("-# can only edit messages sent by the bot")

        # If payload starts with json dict
        if content.strip().startswith("{") and content.strip().endswith("}"):
            try:
                data = json.loads(content.strip())
                new_embed = discord.Embed.from_dict(data)
            except Exception as e:
                return await ctx.send(f"-# invalid json: {str(e).lower()}")
        else:
            new_embed, err = _build_embed_from_text(content, author_member=ctx.author, attachments=ctx.message.attachments)
            if err:
                return await ctx.send(f"-# {err}")

        try:
            await target_msg.edit(embed=new_embed)
            await ctx.message.add_reaction("✓")
        except discord.HTTPException as e:
            await ctx.send(f"-# failed to edit message: {str(e).lower()}")


async def setup(bot: commands.Bot):
    await bot.add_cog(EmbedMaker(bot))
