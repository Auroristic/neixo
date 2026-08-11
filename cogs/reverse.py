"""
cogs/reverse.py  —  .reverse — reverse image search
SauceNAO first (anime-grade sources, needs SAUCENAO_KEY), Google Lens
direct-link fallback (Google no longer ships server-side match data, so
the best we can do keylessly is hand over the lens results page).
"""

import logging
import os

import aiohttp
import discord
from discord.ext import commands

from utils import get_embed_color, help_meta

log = logging.getLogger(__name__)

COG_META = {
    "category": "utility",
    "label": "Utility",
    "desc": "Reverse image search.",
}

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
SAUCENAO_API = "https://saucenao.com/search.php"
LENS_UPLOAD = "https://lens.google.com/v3/upload"
MAX_BYTES = 8 * 1024 * 1024
MIN_SIMILARITY = 50.0


def _first_image(message: discord.Message) -> str | None:
    if message.attachments:
        return message.attachments[0].url
    if message.embeds:
        for e in message.embeds:
            if e.image and e.image.url:
                return e.image.url
            if e.thumbnail and e.thumbnail.url:
                return e.thumbnail.url
    return None


def _resolve_source(ctx: commands.Context, url: str | None) -> str | None:
    if url and url.startswith(("http://", "https://")):
        return url
    if ctx.message.attachments:
        return ctx.message.attachments[0].url
    if ctx.message.reference and ctx.message.reference.resolved:
        return _first_image(ctx.message.reference.resolved)
    if ctx.message.embeds:
        for e in ctx.message.embeds:
            if e.image and e.image.url:
                return e.image.url
            if e.thumbnail and e.thumbnail.url:
                return e.thumbnail.url
    return None


async def _download(url: str) -> bytes | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers={"User-Agent": _BROWSER_UA}, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    log.warning("reverse: download %s -> %s", url, r.status)
                    return None
                data = await r.read()
                if len(data) > MAX_BYTES or len(data) == 0:
                    return None
                return data
    except Exception:
        log.warning("reverse: download failed", exc_info=True)
        return None


async def _saucenao(data: bytes, api_key: str) -> list[dict]:
    form = aiohttp.FormData()
    form.add_field("file", data, filename="image.png", content_type="image/png")
    form.add_field("api_key", api_key)
    form.add_field("output_type", "2")
    form.add_field("db", "999")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(SAUCENAO_API, data=form, timeout=aiohttp.ClientTimeout(total=25)) as r:
                if r.status != 200:
                    log.warning("reverse: saucenao -> %s", r.status)
                    return []
                payload = await r.json(content_type=None)
    except Exception:
        log.warning("reverse: saucenao failed", exc_info=True)
        return []
    out = []
    for res in payload.get("results") or []:
        h, d = res.get("header", {}), res.get("data", {})
        try:
            sim = float(h.get("similarity", 0))
        except (TypeError, ValueError):
            continue
        if sim < MIN_SIMILARITY:
            continue
        urls = [u for u in (d.get("ext_urls") or []) if str(u).startswith("http")]
        name = h.get("index_name") or "source"
        title = d.get("title") or d.get("source") or d.get("member_name") or d.get("creator") or name
        out.append(
            {
                "similarity": sim,
                "name": name,
                "title": str(title),
                "url": urls[0] if urls else None,
                "thumb": h.get("thumbnail"),
            }
        )
    return out[:3]


async def _lens_url(data: bytes) -> str | None:
    """Upload to Google Lens (v3 endpoint, consent cookie bypass).
    Google renders results client-side only, so we return the results
    page URL for the user to open — no data to scrape server-side."""
    form = aiohttp.FormData()
    form.add_field("encoded_image", data, filename="image.png", content_type="image/png")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                LENS_UPLOAD,
                data=form,
                headers={"User-Agent": _BROWSER_UA, "Cookie": "SOCS=CAI"},
                timeout=aiohttp.ClientTimeout(total=25),
            ) as r:
                if r.status != 200:
                    return None
                return str(r.url)
    except Exception:
        log.warning("reverse: lens upload failed", exc_info=True)
        return None


class Reverse(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="reverse", aliases=["rv", "saucenao"])
    @help_meta(
        usage=".reverse [image url]",
        desc="Reverse image search — reply to an image, attach one, or pass a url.",
        section="Utility",
        examples=[".reverse", ".reverse <image url>", ".reverse <@image-message>"],
        params=[
            {
                "name": "image url",
                "type": "str",
                "required": False,
                "desc": "Direct link to the image. Leave empty to use an attachment or replied-to image.",
            },
        ],
        note="saucenao first (anime sources), google lens fallback. needs SAUCENAO_KEY in .env.",
    )
    async def reverse(self, ctx: commands.Context, url: str = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        source = _resolve_source(ctx, url)
        if not source:
            return await ctx.send("-# attach an image, pass a url, or reply to a message with one.")

        data = await _download(source)
        if data is None:
            return await ctx.send("-# couldn't download that image.")

        api_key = os.environ.get("SAUCENAO_KEY", "")
        if api_key:
            results = await _saucenao(data, api_key)
            if results:
                return await ctx.send(embed=_build_embed(ctx, "saucenao", results, source))
            log.info("reverse: saucenao no match >= %.0f%%", MIN_SIMILARITY)
        else:
            log.warning("reverse: no SAUCENAO_KEY in env, lens only")

        page_url = await _lens_url(data)
        embed = _build_embed(ctx, "google lens", [], source)
        if page_url:
            embed.description = f"no saucenao match — [open this image in google lens]({page_url})"
        else:
            embed.description = "no matches found anywhere. 😔"
        return await ctx.send(embed=embed)


def _build_embed(ctx: commands.Context, engine: str, results: list[dict], source: str) -> discord.Embed:
    embed = discord.Embed(
        title="reverse search results",
        color=get_embed_color(ctx.guild.id),
    )
    if results:
        lines = []
        for i, r in enumerate(results, 1):
            if r["url"]:
                lines.append(f"{i}. ⭐ {r['similarity']:.1f}% — **{r['name']}** — [{r['title'][:60]}]({r['url']})")
            else:
                lines.append(f"{i}. ⭐ {r['similarity']:.1f}% — **{r['name']}** — {r['title'][:60]}")
        embed.description = "\n".join(lines)
        if results[0].get("thumb"):
            embed.set_thumbnail(url=results[0]["thumb"])
    else:
        embed.description = "no matches found. 😔"
    embed.set_image(url=source)
    embed.set_footer(text=f"{engine} · searched this image")
    return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(Reverse(bot))
