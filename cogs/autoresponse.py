"""
cogs/autoresponse.py  —  custom trigger → reply (per server, admin managed)
"""

import logging
import time

import discord
from discord.ext import commands

from utils import DATA_DIR, get_embed_color, help_meta, is_owner_or_creator, load_json, save_json

log = logging.getLogger(__name__)

AUTO_FILE = f"{DATA_DIR}/autoresponses.json"

COG_META = {
    "category": "general",
    "label": "General",
    "desc": "Custom auto-responses.",
}

_COOLDOWN_SECONDS = 5
_MAX_RESPONSE = 1500


def _load_auto() -> dict:
    return load_json(AUTO_FILE) or {}


def _save_auto(state: dict) -> None:
    save_json(AUTO_FILE, state)


class AutoResponse(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # (guild_id, trigger) -> last reply timestamp
        self._cooldowns: dict[tuple[int, str], float] = {}

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
        desc="Custom auto-responses: when someone says the trigger, the bot replies.",
        section="General",
        examples=[".auto add hello => hi there", ".auto remove hello", ".auto list"],
        params=[],
        note="admin only. placeholders: `{user}`, `{server}`, `{channel}`. max 15 triggers per server.",
    )
    async def auto(self, ctx: commands.Context, *, args: str = None):
        if not args:
            return await ctx.send("-# usage: `.auto add <trigger> => <response>` · `.auto remove <trigger>` · `.auto list`")
        parts = args.split("=>", 1)
        if len(parts) == 2:
            trigger, response = parts[0].strip().lower(), parts[1].strip()
        else:
            words = args.split(None, 1)
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
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    @auto.command(name="remove")
    @help_meta(
        usage="`.auto remove <trigger>`",
        desc="Removes an auto-response trigger.",
        section="General",
        examples=[".auto remove hello"],
        params=[{"name": "trigger", "type": "str", "required": True, "desc": "The trigger to remove."}],
        note="admin only.",
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
        desc="Lists all auto-responses in this server.",
        section="General",
        examples=[".auto list"],
        params=[],
        note="anyone can view.",
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
        desc="Removes every auto-response in this server.",
        section="General",
        examples=[".auto clear"],
        params=[],
        note="admin only.",
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
        low = content.lower()
        now = time.time()
        for trigger, response in triggers.items():
            if trigger not in low:
                continue
            key = (message.guild.id, trigger)
            if now - self._cooldowns.get(key, 0.0) < _COOLDOWN_SECONDS:
                continue
            self._cooldowns[key] = now
            reply = (
                response.replace("{user}", message.author.mention)
                .replace("{server}", message.guild.name)
                .replace("{channel}", message.channel.mention)
            )
            try:
                await message.channel.send(reply, allowed_mentions=discord.AllowedMentions(users=[message.author]))
            except discord.HTTPException:
                pass
            return


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoResponse(bot))
