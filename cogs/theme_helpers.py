from __future__ import annotations

import ipaddress
import logging

import aiohttp
import discord
from discord.ext import commands

from neixoconfig import Neixocolor, Neixoemojis
from utils import get_embed_color, is_owner_or_creator

log = logging.getLogger(__name__)

_MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_safe_url(url: str) -> bool:
    import socket
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False
        addr_infos = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
        for family, _, _, _, sockaddr in addr_infos:
            ip = ipaddress.ip_address(sockaddr[0])
            if any(ip in net for net in _BLOCKED_NETWORKS):
                return False
    except Exception:
        return False
    return True

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
        if not _is_safe_url(source):
            raise RuntimeError("blocked: URL resolves to a private/reserved network")
        s = await _get_http_session()
        async with s.get(source, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                raise RuntimeError(f"download failed: HTTP {r.status}")
            data = await r.read()
            if len(data) > _MAX_DOWNLOAD_BYTES:
                raise RuntimeError(f"file too large ({len(data)} bytes, max {_MAX_DOWNLOAD_BYTES})")
            return data
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
