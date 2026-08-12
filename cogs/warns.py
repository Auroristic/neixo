"""
cogs/warns.py  —  warn system: warnings, auto-timeout at 3, auto-ban at 5
"""

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from utils import DATA_DIR, get_embed_color, help_meta, is_owner_or_creator, load_json, save_json

log = logging.getLogger(__name__)

WARNS_FILE = f"{DATA_DIR}/warns.json"
WARNLOG_FILE = f"{DATA_DIR}/warnlog.json"

TIMEOUT_AT = 3      # warns before timeout
TIMEOUT_SECONDS = 3600
BAN_AT = 5          # warns before ban

COG_META = {
    "category": "moderation",
    "label": "Moderation",
    "desc": "Warn system: warnings, auto-timeout, auto-ban.",
}


def _load_warns() -> dict:
    return load_json(WARNS_FILE) or {}


def _save_warns(state: dict) -> None:
    save_json(WARNS_FILE, state)


class Warns(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _staff(self, ctx) -> bool:
        if ctx.guild is None:
            return False
        if is_owner_or_creator(ctx):
            return True
        perms = getattr(ctx.author, "guild_permissions", None)
        return bool(perms and perms.administrator)

    def _log_channel_id(self, guild_id: int) -> int | None:
        raw = (load_json(WARNLOG_FILE) or {}).get(str(guild_id))
        return int(raw) if raw else None

    async def _post_log(self, guild: discord.Guild, text: str):
        ch_id = self._log_channel_id(guild.id)
        if not ch_id:
            return
        ch = guild.get_channel(ch_id)
        if ch is None:
            return
        try:
            await ch.send(f"-# {text}")
        except discord.HTTPException:
            pass

    @commands.group(name="warn", invoke_without_command=True)
    @help_meta(
        usage="`.warn <@user> <reason>`  ·  `.warnings [@user]`  ·  `.unwarn <@user>`",
        desc="Warns a user. 3 warns = 1h timeout, 5 warns = ban.",
        section="Moderation",
        examples=[".warn @someone spamming", ".warnings", ".unwarn @someone"],
        params=[
            {"name": "user", "type": "discord.Member", "required": True, "desc": "User to warn."},
            {"name": "reason", "type": "str", "required": True, "desc": "Why they're being warned."},
        ],
        note="Staff only. Use `.warn log #channel` to get a log channel.",
    )
    async def warn(self, ctx: commands.Context, user: discord.Member = None, *, reason: str = None):
        if not await self._staff(ctx):
            return await ctx.send("-# staff only")
        if user is None or not reason:
            return await ctx.send("-# usage: `.warn <@user> <reason>`")
        if user.id == ctx.author.id:
            return await ctx.send("-# can't warn yourself, cmon")
        if user.bot:
            return await ctx.send("-# can't warn bots")

        state = _load_warns()
        gid = str(ctx.guild.id)
        uid = str(user.id)
        entry = state.setdefault(gid, {}).setdefault(uid, {"warns": []})
        entry["warns"].append({
            "reason": reason.strip()[:300],
            "by": str(ctx.author.id),
            "at": datetime.now(timezone.utc).isoformat(),
        })
        count = len(entry["warns"])
        _save_warns(state)

        msg = f"warned {user.mention} — **{count}**/{BAN_AT} ({reason.strip()[:100]})"

        # auto-timeout at 3 warns
        if count == TIMEOUT_AT:
            try:
                await user.timeout(
                    timedelta(seconds=TIMEOUT_SECONDS),
                    reason=f"auto-timeout at {TIMEOUT_AT} warns",
                )
                msg += f"\n-# auto-timed out for {TIMEOUT_SECONDS // 60}min"
                entry["last_timeout_iso"] = datetime.now(timezone.utc).isoformat()
                _save_warns(state)
            except discord.HTTPException:
                msg += "\n-# couldn't timeout (missing perms?)"

        # auto-ban at 5 warns
        if count >= BAN_AT:
            try:
                await ctx.guild.ban(user, reason=f"auto-ban at {BAN_AT} warns")
                msg += f"\n-# banned."
            except discord.HTTPException:
                msg += f"\n-# couldn't ban (missing perms?)"

        await ctx.send(f"-# {msg}")
        await self._post_log(ctx.guild, f"`{ctx.author.display_name}` warned <@{user.id}> ({count}/{BAN_AT}) — {reason.strip()[:100]}")

    @warn.group(name="log", invoke_without_command=True)
    @help_meta(
        usage="`.warn log [#channel]`",
        desc="Shows or sets the warn log channel.",
        section="Moderation",
        examples=[".warn log", ".warn log #staff-logs"],
        params=[
            {"name": "channel", "type": "discord.TextChannel", "required": False, "desc": "Channel to post warn logs to. Omit to show the current one."},
        ],
        note="Staff only.",
    )
    async def warn_log(self, ctx: commands.Context, channel: discord.TextChannel = None):
        if not await self._staff(ctx):
            return await ctx.send("-# staff only")
        if channel is None:
            ch_id = self._log_channel_id(ctx.guild.id)
            if not ch_id:
                return await ctx.send("-# no warn log channel set. `.warn log #channel` to set one.")
            ch = ctx.guild.get_channel(ch_id)
            return await ctx.send(f"-# warn log on in {ch.mention if ch else ch_id}.")
        state = load_json(WARNLOG_FILE) or {}
        state[str(ctx.guild.id)] = str(channel.id)
        save_json(WARNLOG_FILE, state)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    @warn.command(name="clear")
    @help_meta(
        usage="`.warn clear <@user>`",
        desc="Clears all warns for a user.",
        section="Moderation",
        examples=[".warn clear @someone"],
        params=[
            {"name": "user", "type": "discord.Member", "required": True, "desc": "User whose warns to clear."},
        ],
        note="Staff only. This cannot be undone.",
    )
    async def warn_clear(self, ctx: commands.Context, user: discord.Member = None):
        if not await self._staff(ctx):
            return await ctx.send("-# staff only")
        if user is None:
            return await ctx.send("-# usage: `.warn clear <@user>`")
        state = _load_warns()
        gid = str(ctx.guild.id)
        if state.get(gid, {}).pop(str(user.id), None):
            _save_warns(state)
            await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
            await self._post_log(ctx.guild, f"`{ctx.author.display_name}` cleared all warns for <@{user.id}>")
        else:
            await ctx.send(f"-# {user.display_name} has no warns.")

    @commands.command(name="unwarn")
    @help_meta(
        usage="`.unwarn <@user>`",
        desc="Removes the most recent warn from a user.",
        section="Moderation",
        examples=[".unwarn @someone"],
        params=[{"name": "user", "type": "discord.Member", "required": True, "desc": "User to unwarn."}],
        note="staff only.",
    )
    async def unwarn(self, ctx: commands.Context, user: discord.Member = None):
        if not await self._staff(ctx):
            return await ctx.send("-# staff only")
        if user is None:
            return await ctx.send("-# usage: `.unwarn <@user>`")
        state = _load_warns()
        gid = str(ctx.guild.id)
        entry = state.get(gid, {}).get(str(user.id))
        if not entry or not entry.get("warns"):
            return await ctx.send(f"-# {user.display_name} has no warns.")
        removed = entry["warns"].pop()
        _save_warns(state)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await self._post_log(ctx.guild, f"`{ctx.author.display_name}` unwarned <@{user.id}> — removed: {removed.get('reason', '?')[:100]}")

    @commands.command(name="warnings", aliases=["warns"])
    @help_meta(
        usage="`.warnings [@user]`",
        desc="Shows warns for a user (or your own).",
        section="Moderation",
        examples=[".warnings", ".warnings @someone"],
        params=[{"name": "user", "type": "discord.Member", "required": False, "desc": "User to check. Defaults to you."}],
        note="Staff can check anyone's warns.",
    )
    async def warnings(self, ctx: commands.Context, user: discord.Member = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        target = user or ctx.author
        if user and not await self._staff(ctx):
            return await ctx.send("-# staff only for checking others")
        entry = _load_warns().get(str(ctx.guild.id), {}).get(str(target.id))
        warns = entry.get("warns", []) if entry else []
        if not warns:
            return await ctx.send(f"-# {target.display_name} is clean. no warns.")
        lines = []
        for i, w in enumerate(reversed(warns), start=1):
            try:
                when = datetime.fromisoformat(w["at"]).strftime("%b %d")
            except Exception:
                when = "?"
            lines.append(f"`#{len(warns) - i + 1}` **{when}** — {w['reason'][:120]}")
        embed = discord.Embed(
            title=f"warns — {target.display_name}",
            description="\n".join(lines),
            color=get_embed_color(ctx.guild.id),
        )
        embed.set_footer(text=f"{len(warns)}/{BAN_AT} warns · {TIMEOUT_AT} = timeout · {BAN_AT} = ban")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Warns(bot))
