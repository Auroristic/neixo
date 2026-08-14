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

COG_META = {
    "category": "fun",
    "label": "Fun",
    "desc": "Anime action gifs.",
}

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
        # gifukai first (latest, pairing-aware), nekos.best as fallback
        for api, pick in (
            (GIFUKAI_API.format(action), lambda d: d.get("url")),
            (NEKOS_API.format(action), lambda d: (d.get("results") or [{}])[0].get("url")),
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
def _action_func(name: str, verb: str):
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
            embed.set_footer(text=f"/{name} \u00b7 anime gif")
        else:
            embed.set_footer(text="gif fetch failed, use your imagination")
        await ctx.send(embed=embed)

    cmd.__name__ = name
    cmd.__qualname__ = f"Actions.{name}"
    return cmd


def _build_command(name: str, verb: str) -> commands.Command:
    func = _action_func(name, verb)
    help_meta(
        usage=f".{name} [@user]",
        desc=f"Anime reaction GIF — {verb} someone or react to a situation.",
        section="Fun",
        perm_tier="public",
        examples=[f".{name}", f".{name} @someone"],
        params=[
            {
                "name": "user",
                "type": "user",
                "required": False,
                "desc": "Member to direct the action towards. Omit to perform on yourself.",
            },
        ],
        note="Anime reaction GIFs via gifukai with nekos.best fallback.",
    )(func)
    return commands.Command(func, name=name)


class Actions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    cog = Actions(bot)
    # register dynamically: add_cog registers everything in __cog_commands__
    # and _inject binds command.cog — do NOT also call bot.add_command (that
    # double-registers and raises CommandRegistrationError)
    extra = tuple(_build_command(name, verb) for name, verb in _ACTIONS)
    cog.__cog_commands__ = tuple(list(cog.__cog_commands__) + list(extra))
    await bot.add_cog(cog)
