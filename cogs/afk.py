"""
cogs/afk.py  —  afk status with auto-reply when mentioned
"""

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from utils import DATA_DIR, get_embed_color, help_meta, load_json, save_json

log = logging.getLogger(__name__)

AFK_FILE = f"{DATA_DIR}/afk.json"

COG_META = {
    "category": "utility",
    "label": "Utility",
    "desc": "AFK status management with auto-mentions and auto-clear.",
}


def _load_afk() -> dict:
    return load_json(AFK_FILE) or {}


def _save_afk(state: dict) -> None:
    save_json(AFK_FILE, state)


def _ago(iso: str) -> str:
    try:
        then = datetime.fromisoformat(iso)
        delta = datetime.now(timezone.utc) - then
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m"
        if secs < 86400:
            return f"{secs // 3600}h"
        return f"{secs // 86400}d"
    except Exception:
        return "?"


class AFK(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # guild_id -> {user_id -> {"reason": str, "since": str}}
        self._afk: dict[str, dict] = {}

    async def cog_load(self):
        self._afk = _load_afk() or {}

    def _save(self):
        _save_afk(self._afk)

    def _afk_of(self, guild_id: int, user_id: int) -> dict | None:
        return self._afk.get(str(guild_id), {}).get(str(user_id))

    @commands.command(name="afk")
    @help_meta(
        usage="`.afk [reason]`",
        desc="Sets your status to AFK. Informs members who mention you and auto-clears on return.",
        section="Utility",
        perm_tier="public",
        examples=[".afk", ".afk grabbing food", ".afk sleeping"],
        params=[
            {
                "name": "reason",
                "type": "str",
                "required": False,
                "desc": "Optional status message displayed when someone pings you.",
            },
        ],
        note="Send any standard message in the server to automatically remove your AFK status.",
    )
    async def afk(self, ctx: commands.Context, *, reason: str = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        gid = str(ctx.guild.id)
        self._afk.setdefault(gid, {})
        self._afk[gid][str(ctx.author.id)] = {
            "reason": (reason.strip()[:200] if reason else ""),
            "since": datetime.now(timezone.utc).isoformat(),
        }
        self._save()
        embed = discord.Embed(
            description=(
                f"you're afk now {ctx.author.mention}"
                + (f" — **{reason.strip()[:200]}**" if reason else "")
            ),
            color=get_embed_color(ctx.guild.id),
        )
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        content = message.content or ""
        is_cmd = content.startswith(".") or (
            self.bot.user and content.startswith(f"{self.bot.user.mention} ")
        )

        if is_cmd:
            return

        # returning: any non-command message clears the afk status
        entry = self._afk_of(message.guild.id, message.author.id)
        if entry:
            self._afk.get(str(message.guild.id), {}).pop(str(message.author.id), None)
            self._save()
            embed = discord.Embed(
                description=(
                    f"welcome back {message.author.mention} — was "
                    f"afk for {_ago(entry['since'])}"
                    + (f" (\u201c{entry['reason']}\u201d)" if entry["reason"] else "")
                ),
                color=get_embed_color(message.guild.id),
            )
            try:
                await message.reply(embed=embed)
            except discord.HTTPException:
                pass
            return

        # someone mentioned an afk member
        guild_afk = self._afk.get(str(message.guild.id), {})
        if not guild_afk:
            return
        afk_mentioned = [
            m for m in message.mentions if str(m.id) in guild_afk and m.id != message.author.id
        ]
        if not afk_mentioned:
            return
        lines = []
        for m in afk_mentioned:
            entry = guild_afk[str(m.id)]
            lines.append(
                f"**{m.display_name}** is afk ({_ago(entry['since'])})"
                + (f" — {entry['reason']}" if entry["reason"] else "")
            )
        embed = discord.Embed(
            description="\n".join(lines),
            color=get_embed_color(message.guild.id),
        )
        try:
            await message.reply(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild_afk = self._afk.get(str(member.guild.id))
        if guild_afk and str(member.id) in guild_afk:
            guild_afk.pop(str(member.id), None)
            self._save()


async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))
