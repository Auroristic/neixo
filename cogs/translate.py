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

DETECT_MODEL = "deepseek-ai/deepseek-v4-flash-0731"

# iso 639-1 -> riva code (detection returns the short code)
_ISO_TO_RIVA = {
    "zh": "zh-CN",
    "pt": "pt-PT",
    "es": "es-ES",
    "en": "en",
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
        usage="`.translate <language> <text>`  ·  reply to a message + `.translate [language]`",
        desc="Translates text (English to 36 languages). Replying to a message translates it.",
        section="General",
        examples=[".translate japanese hello there", ".translate from ja this is japanese text"],
        params=[
            {"name": "args", "type": "str", "required": True, "desc": "`<language> <text>` (source is English), `from <language> <text>` (to English), or reply to a message and use `.translate [language]` to translate it (any language → English by default). `.translate langs` for the list."},
        ],
        note="Replying with no args translates the replied message to English. Reply plus `.translate <lang>` goes to that language (source auto-detected).",
    )
    async def translate(self, ctx: commands.Context, *, args: str = None):
        key = _get_key()
        if not key:
            return await ctx.send("-# no nvidia key set. can't translate rn")

        # ── replying to a message: translate THAT message ──
        # resolved BEFORE the usage guard so bare `.translate` on a reply works
        replied = await self._resolved_reply_content(ctx.message)
        if replied:
            if not args:
                async with ctx.typing():
                    detected = await self._detect_lang(key, replied)
                if not detected:
                    return await ctx.send("-# couldn't detect the source language, try `.translate from <lang>`")
                src, tgt, text = detected, "en", replied
            elif args.strip().lower().startswith("from "):
                lang = LANG_CODES.get(args.strip()[5:].strip().lower())
                if not lang:
                    return await ctx.send(f"-# don't know that language. `.translate langs` for the list.")
                src, tgt, text = lang, "en", replied
            else:
                lang = LANG_CODES.get(args.strip().lower())
                if not lang:
                    return await ctx.send(
                        f"-# when replying, the optional arg is the target language (e.g. `.translate japanese`). `.translate langs` for the list."
                    )
                async with ctx.typing():
                    detected = await self._detect_lang(key, replied)
                if not detected:
                    return await ctx.send("-# couldn't detect the source language, try `.translate from <lang>`")
                src, tgt, text = detected, lang, replied
            direction = f"{src} -> {tgt}"
            async with ctx.typing():
                result = await self._call_translate(key, src, tgt, text)
            if result is None:
                return await ctx.send("-# translate call failed. try again in a sec")
            embed = discord.Embed(
                description=result,
                color=get_embed_color(ctx.guild.id) if ctx.guild else 0x121516,
            )
            embed.set_author(name=direction)
            footer = replied[:120] + ("…" if len(replied) > 120 else "")
            embed.set_footer(text=footer)
            return await ctx.send(embed=embed)

        if not args:
            return await ctx.send("-# usage: `.translate <language> <text>` · `.translate from <language> <text>` · or reply to a message + `.translate`")

        if args.strip().lower() == "langs":
            names = ", ".join(sorted(set(DISPLAY.values())))
            return await ctx.send(f"-# supported: {names}")

        # ── normal text translation ──
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

    async def _detect_lang(self, key: str, text: str) -> str | None:
        """Detect the language code of a text snippet (cheap deepseek call)."""
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": DETECT_MODEL,
            "messages": [
                {"role": "user", "content": (
                    "Reply with ONLY the ISO 639-1 code of the language this text is in. "
                    "No other text, no punctuation.\nText: " + text[:400]
                )},
            ],
            "max_tokens": 5,
            "temperature": 0.0,
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(TRANSLATE_URL, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            code = data["choices"][0]["message"]["content"].strip().lower()[:5]
            return _ISO_TO_RIVA.get(code, code)
        except Exception:
            return None

    async def _resolved_reply_content(self, message: discord.Message) -> str | None:
        if not message.reference:
            return None
        ref = message.reference.resolved
        if isinstance(ref, discord.Message) and ref.content and not ref.author.bot:
            return ref.content.strip()[:1500]
        if message.reference.message_id and message.guild:
            try:
                ref = await message.channel.fetch_message(message.reference.message_id)
                if ref.content and not ref.author.bot:
                    return ref.content.strip()[:1500]
            except discord.HTTPException:
                pass
        return None

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
