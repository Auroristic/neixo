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

# ── Smart Slot Resolver ──────────────────────────────────────────

def _resolve_role_slot(
    guild: discord.Guild | None,
    role_map: dict[str, int | str],
    raw_args: str | None
) -> tuple[str | None, discord.Role | None, str]:
    """
    Intelligently resolves slot from user input.
    Supports:
      1. 1-based numeric index: '1', '1 true dragon', '#1 true dragon'
      2. Role mention or ID: '<@&12345> true dragon', '123456789'
      3. Multi-word slot names: 'co owner true dragon', 'head of security Chief'
      4. Case-insensitive & normalized names: 'owner', 'Owner', 'OWNER'
    Returns:
      (slot_name, role_object, remaining_arguments)
    """
    import re
    if not raw_args or not role_map:
        return None, None, ""

    raw = raw_args.strip()
    slots = list(role_map.keys())

    # 1. 1-based index (e.g. "1", "1 true dragon", "#1 true dragon", "1. true dragon")
    match_num = re.match(r"^#?(\d+)[.:\-]?\s*(.*)$", raw)
    if match_num:
        idx = int(match_num.group(1))
        if 1 <= idx <= len(slots):
            slot_name = slots[idx - 1]
            rid = int(role_map[slot_name])
            role = guild.get_role(rid) if guild else None
            rest = match_num.group(2).strip()
            return slot_name, role, rest

    # 2. Role mention or ID (e.g. "<@&12345> true dragon")
    match_mention = re.match(r"^<@&?(\d+)>\s*(.*)$", raw)
    if match_mention:
        rid = int(match_mention.group(1))
        rest = match_mention.group(2).strip()
        for s_name, s_rid in role_map.items():
            if int(s_rid) == rid:
                role = guild.get_role(rid) if guild else None
                return s_name, role, rest

    # 3. Multi-word and exact name matching (longest match first)
    low_raw = raw.lower()
    for s_name in sorted(slots, key=lambda s: len(s), reverse=True):
        s_low = s_name.lower()
        if low_raw == s_low:
            role = guild.get_role(int(role_map[s_name])) if guild else None
            return s_name, role, ""
        elif low_raw.startswith(s_low + " ") or low_raw.startswith(s_low + ":") or low_raw.startswith(s_low + "-"):
            role = guild.get_role(int(role_map[s_name])) if guild else None
            rest = raw[len(s_name):].lstrip(" :-").strip()
            return s_name, role, rest

    # 4. Normalized matching (ignoring punctuation/spaces, e.g. "coowner" -> "co owner")
    first_token = raw.split()[0] if raw.split() else ""
    norm_first = re.sub(r"[^a-zA-Z0-9]", "", first_token).lower()
    for s_name in slots:
        norm_slot = re.sub(r"[^a-zA-Z0-9]", "", s_name).lower()
        if norm_slot and norm_slot == norm_first:
            role = guild.get_role(int(role_map[s_name])) if guild else None
            rest = " ".join(raw.split()[1:]).strip()
            return s_name, role, rest

    return None, None, raw

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
