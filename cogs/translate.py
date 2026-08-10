"""
cogs/translate.py  —  translation via NVIDIA riva-translate-4b-instruct-v2
"""

import logging
import os

import aiohttp
import discord
from discord.ext import commands

from utils import get_embed_color, help_meta

log = logging.getLogger(__name__)

TRANSLATE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
TRANSLATE_MODEL = "nvidia/riva-translate-4b-instruct-v2"

COG_META = {
    "category": "general",
    "label": "General",
    "desc": "Translation via NVIDIA riva.",
}

# riva language codes (en <-> 36 others)
LANG_CODES = {
    "en": "en",
    "english": "en",
    "cs": "cs",
    "czech": "cs",
    "da": "da",
    "danish": "da",
    "de": "de",
    "german": "de",
    "el": "el",
    "greek": "el",
    "es": "es-ES",
    "es-es": "es-ES",
    "spanish": "es-ES",
    "european spanish": "es-ES",
    "es-us": "es-US",
    "latin spanish": "es-US",
    "latin american spanish": "es-US",
    "fi": "fi",
    "finnish": "fi",
    "fr": "fr",
    "french": "fr",
    "hu": "hu",
    "hungarian": "hu",
    "it": "it",
    "italian": "it",
    "lt": "lt",
    "lithuanian": "lt",
    "lv": "lv",
    "latvian": "lv",
    "nl": "nl",
    "dutch": "nl",
    "no": "no",
    "norwegian": "no",
    "pl": "pl",
    "polish": "pl",
    "pt": "pt-PT",
    "pt-pt": "pt-PT",
    "portuguese": "pt-PT",
    "european portuguese": "pt-PT",
    "pt-br": "pt-BR",
    "brazilian portuguese": "pt-BR",
    "ro": "ro",
    "romanian": "ro",
    "ru": "ru",
    "russian": "ru",
    "sk": "sk",
    "slovak": "sk",
    "sv": "sv",
    "swedish": "sv",
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "chinese": "zh-CN",
    "simplified chinese": "zh-CN",
    "zh-tw": "zh-TW",
    "traditional chinese": "zh-TW",
    "ja": "ja",
    "japanese": "ja",
    "hi": "hi",
    "hindi": "hi",
    "ko": "ko",
    "korean": "ko",
    "et": "et",
    "estonian": "et",
    "sl": "sl",
    "slovenian": "sl",
    "bg": "bg",
    "bulgarian": "bg",
    "uk": "uk",
    "ukrainian": "uk",
    "hr": "hr",
    "croatian": "hr",
    "ar": "ar",
    "arabic": "ar",
    "vi": "vi",
    "vietnamese": "vi",
    "tr": "tr",
    "turkish": "tr",
    "id": "id",
    "indonesian": "id",
    "th": "th",
    "thai": "th",
}

DISPLAY = {
    "en": "english", "cs": "czech", "da": "danish", "de": "german",
    "el": "greek", "es-ES": "spanish", "es-US": "latin spanish",
    "fi": "finnish", "fr": "french", "hu": "hungarian", "it": "italian",
    "lt": "lithuanian", "lv": "latvian", "nl": "dutch", "no": "norwegian",
    "pl": "polish", "pt-PT": "portuguese", "pt-BR": "brazilian portuguese",
    "ro": "romanian", "ru": "russian", "sk": "slovak", "sv": "swedish",
    "zh-CN": "simplified chinese", "zh-TW": "traditional chinese",
    "ja": "japanese", "hi": "hindi", "ko": "korean", "et": "estonian",
    "sl": "slovenian", "bg": "bulgarian", "uk": "ukrainian", "hr": "croatian",
    "ar": "arabic", "vi": "vietnamese", "tr": "turkish", "id": "indonesian",
    "th": "thai",
}

_KEYS = None


def _get_key() -> str | None:
    global _KEYS
    if _KEYS is None:
        _KEYS = [
            k for k in (
                os.getenv("NVIDIA_API_KEY_1"),
                os.getenv("NVIDIA_API_KEY_2"),
                os.getenv("NVIDIA_API_KEY_3"),
            )
            if k
        ]
    return _KEYS[0] if _KEYS else None


class Translate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="translate", aliases=["tr", "trans"])
    @help_meta(
        usage="`.translate <language> <text>`  ·  `.translate from <language> <text>`",
        desc="Translates text with NVIDIA riva (english <-> 36 languages).",
        section="General",
        examples=[".translate japanese hello there", ".translate from ja this is japanese text"],
        params=[
            {"name": "args", "type": "str", "required": True, "desc": "`<language> <text>` (source is english) or `from <language> <text>` (to english). `.translate langs` for the list."},
        ],
        note="default direction is english -> chosen language. use `from <lang>` to go to english.",
    )
    async def translate(self, ctx: commands.Context, *, args: str = None):
        if not args:
            return await ctx.send("-# usage: `.translate <language> <text>` or `.translate from <language> <text>`")

        if args.strip().lower() == "langs":
            names = ", ".join(sorted(set(DISPLAY.values())))
            return await ctx.send(f"-# supported: {names}")

        # parse direction
        parts = args.strip().split(None, 1)
        if parts[0].lower() == "from" and len(parts) > 1:
            inner = parts[1].strip().split(None, 1)
            if len(inner) < 2:
                return await ctx.send("-# usage: `.translate from <language> <text>`")
            lang = LANG_CODES.get(inner[0].lower())
            if not lang:
                return await ctx.send(f"-# don't know `{inner[0]}`. `.translate langs` for the list.")
            src, tgt = lang, "en"
            text = inner[1].strip()
        else:
            lang = LANG_CODES.get(parts[0].lower())
            if not lang:
                return await ctx.send(f"-# don't know `{parts[0]}`. `.translate langs` for the list.")
            if len(parts) < 2 or not parts[1].strip():
                return await ctx.send("-# nothing to translate. `.translate <language> <text>`")
            src, tgt = "en", lang
            text = parts[1].strip()

        if len(text) > 1500:
            text = text[:1500] + "…"
        if len(text) < 1:
            return await ctx.send("-# nothing to translate")

        key = _get_key()
        if not key:
            return await ctx.send("-# no nvidia key set. can't translate rn")

        async with ctx.typing():
            result = await self._call_translate(key, src, tgt, text)
        if result is None:
            return await ctx.send("-# translate call failed. try again in a sec")

        embed = discord.Embed(
            description=result,
            color=get_embed_color(ctx.guild.id) if ctx.guild else 0x121516,
        )
        embed.set_author(name=f"{DISPLAY.get(src, src)} -> {DISPLAY.get(tgt, tgt)}")
        if len(text) > 120:
            footer = text[:120] + "…"
        else:
            footer = text
        embed.set_footer(text=footer)
        await ctx.send(embed=embed)

    async def _call_translate(self, key: str, src: str, tgt: str, text: str) -> str | None:
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": TRANSLATE_MODEL,
            "messages": [
                {"role": "system", "content": f"{src}-{tgt}"},
                {"role": "user", "content": text},
            ],
            "max_tokens": 2048,
            "temperature": 0.0,
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(TRANSLATE_URL, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        log.warning("translate failed: status=%s body=%s", resp.status, (await resp.read())[:200])
                        return None
                    data = await resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            log.warning("translate error: %s", e)
            return None


async def setup(bot: commands.Bot):
    await bot.add_cog(Translate(bot))
