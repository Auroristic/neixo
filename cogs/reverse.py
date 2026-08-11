"""
cogs/reverse.py  —  .reverse — reverse image search
SauceNAO first (anime-grade sources, needs SAUCENAO_KEY), Google Lens
direct-link fallback (Google no longer ships server-side match data, so
the best we can do keylessly is hand over the lens results page).
"""

import base64
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


async def _saucenao(data: bytes, api_key: str, min_sim: float = WEAK_FLOOR) -> list[dict]:
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
        if sim < min_sim:
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
    return out[:8]


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
        usage=".reverse [what] [image url] [question...]",
        desc="Reverse image search — reply to an image, attach one, or pass a url. "
        "Pass a word like `anime`, `movie`, or `character` and the AI identifies it.",
        section="Utility",
        examples=[".reverse", ".reverse anime", ".reverse movie", ".reverse character", ".reverse <image url>"],
        params=[
            {
                "name": "what",
                "type": "str",
                "required": False,
                "desc": "What the AI should identify: anime, movie, character, artist... any non-url word works.",
            },
            {
                "name": "image url",
                "type": "str",
                "required": False,
                "desc": "Direct link to the image. Leave empty to use an attachment or replied-to image.",
            },
            {
                "name": "question",
                "type": "str",
                "required": False,
                "desc": "Optional extra question for the AI (only with the `what` word).",
            },
        ],
        note="saucenao first (anime sources), google lens page fallback.",
    )
    async def reverse(self, ctx: commands.Context, url: str = None, *, question: str = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        ai_mode = url is not None and not url.startswith(("http://", "https://"))
        ask_what = None
        if ai_mode:
            ask_what = url
            url = None
        source = _resolve_source(ctx, url)
        if not source:
            return await ctx.send("-# attach an image, pass a url, or reply to a message with one.")

        data = await _download(source)
        if data is None:
            return await ctx.send("-# couldn't download that image.")

        api_key = os.environ.get("SAUCENAO_KEY", "")
        results = await _saucenao(data, api_key) if api_key else []
        strong = [r for r in results if r["similarity"] >= MIN_SIMILARITY]
        weak = [r for r in results if r["similarity"] < MIN_SIMILARITY][:3]

        if ai_mode:
            if not results and not api_key:
                return await ctx.send("-# ai mode needs a saucenao key for context. set SAUCENAO_KEY in .env.")
            if question:
                q = f"what {ask_what} is this? {question}"
            elif ask_what and ask_what != "anime":
                q = f"what {ask_what} is this? identify it."
            else:
                q = "what is this? identify the anime, character, or artist."
            answer, status_msg = await _ai_identify(ctx.bot, ctx.channel, data, strong, weak, q)
            if answer:
                embed = discord.Embed(
                    title="ai identified",
                    description=answer,
                    color=get_embed_color(ctx.guild.id),
                )
                src = (strong or weak)
                if src and src[0]["url"]:
                    embed.set_footer(text="saucenao source: " + src[0]["url"])
                embed.set_image(url=source)
                if status_msg is not None:
                    try:
                        await status_msg.delete()
                    except discord.HTTPException:
                        pass
                return await ctx.send(embed=embed)
            if status_msg is not None:
                try:
                    await status_msg.delete()
                except discord.HTTPException:
                    pass
            if strong:
                return await ctx.send(embed=_build_embed(ctx, "saucenao", strong, source))
            if weak:
                return await ctx.send(embed=_build_weak_embed(ctx, weak, source, lens=None))
            return await ctx.send("-# ai is unavailable rn, and saucenao found nothing. try again later.")

        if strong:
            return await ctx.send(embed=_build_embed(ctx, "saucenao", strong, source))
        log.info("reverse: saucenao no strong match >= %.0f%% (%d weak)", MIN_SIMILARITY, len(weak))

        page_url = await _lens_url(data)
        if weak:
            return await ctx.send(embed=_build_weak_embed(ctx, weak, source, lens=page_url))
        embed = _build_embed(ctx, "google lens", [], source)
        if page_url:
            embed.description = f"no saucenao match — [open this image in google lens]({page_url})"
        else:
            embed.description = "no matches found anywhere. 😔"
        return await ctx.send(embed=embed)


def _mime_of(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _saucenao_summary(strong: list[dict], weak: list[dict]) -> str:
    lines = []
    for r in strong[:5]:
        line = f"- {r['similarity']:.1f}% {r['name']}: {r['title'][:80]}"
        if r["url"]:
            line += f" ({r['url']})"
        lines.append(line)
    for r in weak[:3]:
        line = f"- {r['similarity']:.1f}% (weak match) {r['name']}: {r['title'][:80]}"
        if r["url"]:
            line += f" ({r['url']})"
        lines.append(line)
    return "\n".join(lines) or "no matches"


async def _ai_identify(bot, channel, data: bytes, strong: list[dict], weak: list[dict], question: str) -> tuple[str | None, str | None]:
    """Ask the AI to identify the image. Returns (answer, status_msg)."""
    ai = bot.get_cog("AI")
    if ai is None:
        return None, None
    b64 = base64.b64encode(data).decode()
    summary = _saucenao_summary(strong, weak)
    user_text = (
        f"reverse image search results for this image:\n{summary}\n\n"
        f"{question}"
    )
    payload = [
        {
            "role": "system",
            "content": (
                "you identify what's in a picture — anime, movie, tv show, "
                "character, artist, game, or anything else — using the image "
                "plus reverse image search results. name the title, character, "
                "artist, or source when you can. wrap the title or source "
                "name in **bold**. lowercase, concise, max 3 sentences. "
                "if you can't tell, say you can't tell."
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{_mime_of(data)};base64,{b64}"},
                },
            ],
        },
    ]
    try:
        response, status_msg, _used_fallback = await ai._call_with_status(
            channel, payload, has_images=True, max_tokens=3000
        )
        text = response.choices[0].message.content
        return (text.strip() or None), status_msg
    except Exception:
        log.warning("reverse: ai identify failed", exc_info=True)
        return None, None


def _build_weak_embed(ctx: commands.Context, results: list[dict], source: str, lens: str | None) -> discord.Embed:
    embed = discord.Embed(
        title="weak matches — below confidence",
        description="nothing matched above 50%, but here are the closest guesses:",
        color=get_embed_color(ctx.guild.id),
    )
    lines = []
    for i, r in enumerate(results, 1):
        if r["url"]:
            lines.append(f"{i}. ⭐ {r['similarity']:.1f}% — **{r['name']}** — [{r['title'][:60]}]({r['url']})")
        else:
            lines.append(f"{i}. ⭐ {r['similarity']:.1f}% — **{r['name']}** — {r['title'][:60]}")
    embed.description += "\n" + "\n".join(lines)
    if lens:
        embed.description += f"\n\n[open this image in google lens]({lens})"
    if results[0].get("thumb"):
        embed.set_thumbnail(url=results[0]["thumb"])
    embed.set_image(url=source)
    embed.set_footer(text="saucenao · low confidence guesses")
    return embed


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
