"""
cogs/autoresponse.py  —  custom trigger → reply (per server, admin managed)
"""

import logging
import random
import re
import time

import discord
from discord.ext import commands

from utils import DATA_DIR, get_embed_color, help_meta, is_owner_or_creator, load_json, save_json

log = logging.getLogger(__name__)

AUTO_FILE = f"{DATA_DIR}/autoresponses.json"

COG_META = {
    "category": "admin",
    "label": "Admin",
    "desc": "Custom keyword trigger and auto-reply system with dynamic template variables.",
}

_COOLDOWN_SECONDS = 5
_MAX_RESPONSE = 1500


def _load_auto() -> dict:
    return load_json(AUTO_FILE) or {}


def _save_auto(state: dict) -> None:
    save_json(AUTO_FILE, state)


def _format_response(template: str, message: discord.Message) -> str:
    """Formats dynamic placeholders in the autoresponse template."""
    author = message.author
    guild = message.guild
    channel = message.channel

    res = template
    # User variables
    res = res.replace("{user}", author.mention)
    res = res.replace("{user.mention}", author.mention)
    res = res.replace("{user.name}", author.name)
    res = res.replace("{user.display_name}", author.display_name)
    res = res.replace("{user.nick}", author.display_name)
    res = res.replace("{user.id}", str(author.id))
    res = res.replace("{user.avatar}", author.display_avatar.url)
    res = res.replace("{avatar}", author.display_avatar.url)

    # Server variables
    if guild:
        res = res.replace("{server}", guild.name)
        res = res.replace("{server.name}", guild.name)
        res = res.replace("{server.id}", str(guild.id))
        res = res.replace("{server.count}", str(guild.member_count or 0))
        res = res.replace("{server.members}", str(guild.member_count or 0))

    # Channel variables
    if channel:
        res = res.replace("{channel}", channel.mention if hasattr(channel, "mention") else str(channel.name))
        res = res.replace("{channel.mention}", channel.mention if hasattr(channel, "mention") else str(channel.name))
        res = res.replace("{channel.name}", channel.name if hasattr(channel, "name") else "")

    # Random number: {random:1-100}
    def _repl_num(match):
        try:
            low = int(match.group(1))
            high = int(match.group(2))
            return str(random.randint(min(low, high), max(low, high)))
        except Exception:
            return match.group(0)

    res = re.sub(r"\{random:(\d+)-(\d+)\}", _repl_num, res)

    # Random choice: {random:apple|banana|cherry}
    def _repl_choice(match):
        choices = [c.strip() for c in match.group(1).split("|") if c.strip()]
        return random.choice(choices) if choices else ""

    res = re.sub(r"\{random:([^{}]+?\|[^{}]+?)\}", _repl_choice, res)

    return res


class AutoResponse(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # (guild_id, trigger) -> last reply timestamp
        self._cooldowns: dict[tuple[int, str], float] = {}
        # trigger -> compiled regex pattern
        self._patterns: dict[str, re.Pattern] = {}

    def _get_pattern(self, trigger: str) -> re.Pattern:
        if trigger not in self._patterns:
            self._patterns[trigger] = re.compile(rf"\b{re.escape(trigger)}\b", re.IGNORECASE)
        return self._patterns[trigger]

    async def _admin(self, ctx) -> bool:
        if ctx.guild is None:
            return False
        if is_owner_or_creator(ctx):
            return True
        perms = getattr(ctx.author, "guild_permissions", None)
        return bool(perms and perms.administrator)

    @commands.group(name="auto", aliases=["autoresponse"], invoke_without_command=True)
    @help_meta(
        usage="`.auto add <trigger> => <response>`  ·  `.auto remove <trigger>`  ·  `.auto list`",
        desc="Creates automated keyword responses with rich variables like `{user.mention}`, `{server.name}`, `{server.count}`.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[
            ".auto add hello => Hey {user.mention}, welcome to {server.name}!",
            ".auto add count => We currently have {server.count} members.",
            ".auto add roll => {user.name} rolled a {random:1-6}!",
            ".auto add coin => {user.name} flipped {random:Heads|Tails}!",
            ".auto list",
            ".auto remove hello",
        ],
        params=[
            {"name": "trigger", "type": "str", "required": False, "desc": "Keyword or phrase that triggers the bot."},
            {"name": "response", "type": "str", "required": False, "desc": "Reply text supporting `{user}`, `{user.name}`, `{server.name}`, `{server.count}`, `{channel}`, `{random:1-100}` placeholders."},
        ],
        note="Requires Administrator or Manage Server permission. Max 15 triggers per server.",
    )
    async def auto(self, ctx: commands.Context, *, args: str = None):
        if not await self._admin(ctx):
            return await ctx.send("-# admin only")
        if not args:
            return await ctx.send("-# usage: `.auto add <trigger> => <response>` · `.auto remove <trigger>` · `.auto list`")
        raw = args.strip()
        if raw.lower().startswith("add "):
            raw = raw[4:].strip()
        parts = raw.split("=>", 1)
        if len(parts) == 2:
            trigger, response = parts[0].strip().lower(), parts[1].strip()
        else:
            words = raw.split(None, 1)
            trigger, response = words[0].strip().lower(), (words[1].strip() if len(words) > 1 else "")
        if not trigger or not response:
            return await ctx.send("-# usage: `.auto add <trigger> => <response>`")
        if len(response) > _MAX_RESPONSE:
            return await ctx.send(f"-# response too long (max {_MAX_RESPONSE} chars)")

        state = _load_auto()
        gid = str(ctx.guild.id)
        if trigger in state.get(gid, {}):
            return await ctx.send(f"-# `{trigger}` already exists. remove it first or use `.auto remove`")
        if len(state.get(gid, {})) >= 15:
            return await ctx.send("-# max 15 triggers per server")
        state.setdefault(gid, {})[trigger] = response
        _save_auto(state)
        # compile and cache trigger pattern
        self._get_pattern(trigger)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    @auto.command(name="remove")
    @help_meta(
        usage="`.auto remove <trigger>`",
        desc="Removes a configured auto-response trigger.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".auto remove hello"],
        params=[{"name": "trigger", "type": "str", "required": True, "desc": "The trigger keyword to delete."}],
        note="Requires Administrator permission.",
    )
    async def auto_remove(self, ctx: commands.Context, trigger: str = None):
        if not await self._admin(ctx):
            return await ctx.send("-# admin only")
        if not trigger:
            return await ctx.send("-# usage: `.auto remove <trigger>`")
        state = _load_auto()
        gid = str(ctx.guild.id)
        if state.get(gid, {}).pop(trigger.strip().lower(), None):
            _save_auto(state)
            await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        else:
            await ctx.send(f"-# no trigger named `{trigger.strip().lower()}`")

    @auto.command(name="list")
    @help_meta(
        usage="`.auto list`",
        desc="Lists all active auto-responses configured in this server.",
        section="Server Management",
        perm_tier="public",
        examples=[".auto list"],
        params=[],
        note="Available to all members.",
    )
    async def auto_list(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        state = _load_auto()
        triggers = state.get(str(ctx.guild.id), {})
        if not triggers:
            return await ctx.send("-# no auto-responses set")
        lines = [f"**`{t}`** → {r[:80]}" for t, r in sorted(triggers.items())]
        await ctx.send(embed=discord.Embed(
            title="auto-responses",
            description="\n".join(lines),
            color=get_embed_color(ctx.guild.id),
        ))

    @auto.command(name="clear")
    @help_meta(
        usage="`.auto clear`",
        desc="Removes all auto-response triggers from this server.",
        section="Server Management",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".auto clear"],
        params=[],
        note="Requires Administrator permission. This cannot be undone.",
    )
    async def auto_clear(self, ctx: commands.Context):
        if not await self._admin(ctx):
            return await ctx.send("-# admin only")
        state = _load_auto()
        gid = str(ctx.guild.id)
        if state.pop(gid, None):
            _save_auto(state)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        content = message.content or ""
        if content.startswith(".") or (
            self.bot.user and content.startswith(f"{self.bot.user.mention} ")
        ):
            return

        state = _load_auto()
        triggers = state.get(str(message.guild.id), {})
        if not triggers:
            return
        now = time.time()
        for trigger, raw_response in triggers.items():
            pat = self._get_pattern(trigger)
            if not pat.search(content):
                continue
            key = (message.guild.id, trigger)
            if now - self._cooldowns.get(key, 0.0) < _COOLDOWN_SECONDS:
                continue
            self._cooldowns[key] = now

            reply = _format_response(raw_response, message)
            try:
                await message.channel.send(reply, allowed_mentions=discord.AllowedMentions(users=[message.author]))
            except discord.HTTPException:
                pass
            return


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoResponse(bot))
