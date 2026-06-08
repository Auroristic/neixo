from __future__ import annotations

import asyncio
import logging
import time

import aiohttp
import discord
from discord.ext import commands

from neixoconfig import Neixocolor, Neixoemojis
from utils import get_embed_color, is_owner_or_creator

log = logging.getLogger(__name__)

# ── Shared aiohttp session ───────────────────────────────────────

_http_session: aiohttp.ClientSession | None = None

async def _get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None:
        _http_session = aiohttp.ClientSession()
    return _http_session

async def _close_http_session():
    global _http_session
    if _http_session is not None:
        await _http_session.close()
        _http_session = None

# ── Permission check ──────────────────────────────────────────────

def _is_theme_admin():
    async def predicate(ctx: commands.Context) -> bool:
        if is_owner_or_creator(ctx):
            return True
        await ctx.message.add_reaction("<:redlotus:1263556248310386800>")
        return False
    return commands.check(predicate)

# ── Embed helpers ────────────────────────────────────────────────

def _embed(ctx, title: str, description: str = "", color=None) -> discord.Embed:
    c = color or get_embed_color(ctx.guild.id if ctx.guild else 0)
    return discord.Embed(title=title, description=description, color=c)

def _ok_embed(desc: str) -> discord.Embed:
    return discord.Embed(
        description=f"-# {Neixoemojis.get('check')} | {desc}",
        color=Neixocolor,
    )

def _err_embed(desc: str) -> discord.Embed:
    return discord.Embed(
        description=f"-# {Neixoemojis.get('error')} | {desc}",
        color=Neixocolor,
    )

# ── Icon resolver ────────────────────────────────────────────────

async def _resolve_icon_bytes(ctx: commands.Context, source: str | None) -> bytes | None:
    if ctx.message.reference and ctx.message.reference.resolved:
        ref = ctx.message.reference.resolved
        if hasattr(ref, "attachments") and ref.attachments:
            return await ref.attachments[0].read()
    if ctx.message.attachments:
        return await ctx.message.attachments[0].read()
    if source and source.startswith(("http://", "https://")):
        s = await _get_http_session()
        async with s.get(source, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                raise RuntimeError(f"download failed: HTTP {r.status}")
            return await r.read()
    return None

# ── Progress helpers ─────────────────────────────────────────────

def _progress_bar(done: int, total: int, width: int = 10) -> str:
    filled = int(width * done / max(total, 1))
    return "█" * filled + "░" * (width - filled)

async def _edit_progress(
    msg: discord.Message,
    done: int,
    total: int,
    label: str,
    freq: int = 10,
) -> None:
    if done != total and (done % freq) != 0:
        return
    bar = _progress_bar(done, total)
    try:
        await msg.edit(content=f"-# `{bar}` {done}/{total} — {label}")
    except discord.HTTPException:
        log.debug(
            "progress edit failed (done=%s total=%s label=%r)",
            done,
            total,
            label,
            exc_info=True,
        )
