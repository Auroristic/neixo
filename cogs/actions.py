"""
cogs/actions.py  —  anime action gif commands (.hug, .pat, .kiss, ...)
gifs via nekos.best (proper User-Agent required) with gifukai.com fallback.
"""

import asyncio
import logging
import time

import aiohttp
import discord
from discord.ext import commands

from utils import get_embed_color, help_meta

log = logging.getLogger(__name__)

_UA = "Neixo (https://github.com/Auroristic/neixo)"
NEKOS_API = "https://nekos.best/api/v2/{}"
GIFUKAI_API = "https://api.gifukai.com/v1/{}"

# (action -> (url, fetched_at)) tiny cache so repeated commands don't re-fetch
_gif_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 600

# name -> verb used in the embed line (nekos.best first, gifukai fallback)
_ACTIONS = [
    ("hug", "hugs"),
    ("kiss", "kisses"),
    ("pat", "pats"),
    ("slap", "slaps"),
    ("cuddle", "cuddles"),
    ("poke", "pokes"),
    ("highfive", "high-fives"),
    ("dance", "dances with"),
    ("wave", "waves at"),
    ("cry", "cries with"),
    ("smug", "smugs at"),
    ("blush", "blushes at"),
    ("bite", "bites"),
    ("bonk", "bonks"),
    ("kick", "kicks"),
    ("yeet", "yeets"),
    ("handhold", "holds hands with"),
    ("nom", "noms on"),
    ("smile", "smiles at"),
    ("wink", "winks at"),
    ("stare", "stares at"),
    ("laugh", "laughs with"),
    ("tickle", "tickles"),
    ("pout", "pouts at"),
    ("shrug", "shrugs at"),
    ("sleep", "sleeps with"),
    ("peck", "pecks"),
    ("nya", "nyas at"),
    ("happy", "is happy with"),
    ("feed", "feeds"),
    ("punch", "punches"),
    ("kill", "kills"),
    ("hi", "says hi to"),
    ("bye", "says bye to"),
    ("eat", "eats with"),
    ("shy", "is shy with"),
    ("sip", "sips with"),
    ("nod", "nods at"),
    ("run", "runs with"),
    ("carry", "carries"),
    ("clap", "claps for"),
    ("think", "thinks about"),
    ("thumbsup", "gives a thumbs up to"),
    ("handshake", "shakes hands with"),
    ("nope", "nopes at"),
    ("shocked", "is shocked at"),
    ("spin", "spins with"),
    ("wag", "wags at"),
    ("taunt", "taunts"),
    ("bored", "is bored with"),
    ("facepalm", "facepalms at"),
    ("bleh", "blehs at"),
]


async def _fetch_gif(action: str) -> str | None:
    cached = _gif_cache.get(action)
    if cached and time.time() - cached[1] < _CACHE_TTL:
        return cached[0]
    headers = {"User-Agent": _UA}
    async with aiohttp.ClientSession() as s:
        # nekos.best first, gifukai as fallback
        for api, pick in (
            (NEKOS_API.format(action), lambda d: (d.get("results") or [{}])[0].get("url")),
            (GIFUKAI_API.format(action), lambda d: d.get("url")),
        ):
            try:
                async with s.get(api, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as r:
                    if r.status == 200:
                        data = await r.json()
                        url = pick(data)
                        if url:
                            _gif_cache[action] = (url, time.time())
                            return url
            except Exception:
                continue
    return None
def _action_command(name: str, verb: str):
    @help_meta(
        usage=f".{name} [@user]",
        desc=f"Anime gif — {verb} someone.",
        section="Fun",
        examples=[f".{name} @someone"],
        params=[
            {
                "name": "user",
                "type": "discord.Member",
                "required": False,
                "desc": "Who to do it to. Leave empty for the void.",
            },
        ],
        note="anime gifs via waifu.pics.",
    )
    @commands.command(name=name)
    async def cmd(self, ctx: commands.Context, user: discord.Member = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        target = user or ctx.author
        if target == ctx.author:
            line = f"**{ctx.author.display_name}** {verb} themself"
        elif target.bot:
            line = f"**{ctx.author.display_name}** {verb} {target.display_name} (a bot, bold move)"
        else:
            line = f"**{ctx.author.display_name}** {verb} **{target.display_name}**"

        gif = await _fetch_gif(name)
        embed = discord.Embed(
            description=line,
            color=get_embed_color(ctx.guild.id),
        )
        if gif:
            embed.set_image(url=gif)
            embed.set_footer(text=f"/{name} · anime gif")
        else:
            embed.set_footer(text="gif fetch failed, use your imagination")
        await ctx.send(embed=embed)

    return cmd


class Actions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


for _name, _verb in _ACTIONS:
    setattr(Actions, _name, _action_command(_name, _verb))


async def setup(bot: commands.Bot):
    await bot.add_cog(Actions(bot))
