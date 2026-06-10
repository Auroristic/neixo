from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import re
import time
from typing import List, Optional, Tuple

import aiohttp
import discord
import wavelink
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from neixoconfig import Neixocolor, Neixoemojis
from utils import get_embed_color

log = logging.getLogger(__name__)

# ── CONSTANTS ─────────────────────────────────────────────────

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

SPOTIFY_TRACK_RE = re.compile(r"spotify\.com/track/([A-Za-z0-9]+)")
SPOTIFY_PLAYLIST_RE = re.compile(r"spotify\.com/playlist/([A-Za-z0-9]+)")
SPOTIFY_ALBUM_RE = re.compile(r"spotify\.com/album/([A-Za-z0-9]+)")
SPOTIFY_ARTIST_RE = re.compile(r"spotify\.com/artist/([A-Za-z0-9]+)")
SOUNDCLOUD_RE = re.compile(r"soundcloud\.com/")

GENIUS_ACCESS_TOKEN = os.getenv("GENIUS_ACCESS_TOKEN", "")
GENIUS_API_BASE = "https://api.genius.com"

SPOTIFY_PLAYLIST_CAP = 100
MAX_TRACK_DURATION_MS = 30 * 60 * 1000
SPOTIFY_BATCH_SIZE = 5
SEARCH_RETRIES = 2
SEARCH_RETRY_DELAY = 0.5

GENRE_MAP = {
    "phonk": "37i9dQZF1DWWY64wDtewQt",
    "hindi_romantic": "0zc6Hq9OIAengtGG6a3lfs",
    "hindi_sad": "45Jl6Uuj7HVwYrpFwQM0Zs",
    "english": "37i9dQZF1DXcBWIGoYBM5M",
    "viral": "37i9dQZF1DX82GYyH4GL4U",
}

# ── HELPERS ───────────────────────────────────────────────────

def _color_for(source) -> int:
    if source is None:
        return Neixocolor
    try:
        if isinstance(source, int):
            return get_embed_color(source)
        gid = getattr(source, "guild_id", None)
        if gid:
            return get_embed_color(gid)
        guild = getattr(source, "guild", None)
        if guild is not None:
            return get_embed_color(guild.id)
    except Exception:
        pass
    return Neixocolor


def _ok_embed(desc: str, source=None) -> discord.Embed:
    return discord.Embed(
        description=f"-# {Neixoemojis.get('check')} | {desc}",
        color=_color_for(source),
    )

def _err_embed(desc: str, source=None) -> discord.Embed:
    return discord.Embed(
        description=f"-# {Neixoemojis.get('error')} | {desc}",
        color=_color_for(source),
    )

def _fmt_time(ms: int) -> str:
    s = ms // 1000
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"

def _progress_bar(position: int, length: int, width: int = 20) -> str:
    if not length:
        return "░" * width
    filled = int((position / length) * width)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)

async def _edit_progress(msg: discord.Message, done: int, total: int, label: str):
    bar = _progress_bar(done, total)
    try:
        await msg.edit(content=f"-# `{bar}` {done}/{total} — {label}")
    except discord.HTTPException:
        pass

def _is_track_allowed(track) -> Tuple[bool, str]:
    if track is None:
        return False, "no track."
    if getattr(track, "is_stream", False):
        return False, "live streams aren't supported."
    length = getattr(track, "length", 0) or 0
    if length <= 0:
        return False, "track has no duration metadata."
    if length > MAX_TRACK_DURATION_MS:
        return False, (
            f"track is too long ({_fmt_time(length)}). "
            f"max is {_fmt_time(MAX_TRACK_DURATION_MS)}."
        )
    return True, ""

# ── SPOTIFY CLIENT ────────────────────────────────────────────

class SpotifyClient:
    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._expires: float = 0.0
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _ensure_token(self) -> None:
        session = await self._get_session()
        if self._token and time.monotonic() < self._expires:
            return
        creds = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
        headers = {"Authorization": f"Basic {creds}"}
        data = {"grant_type": "client_credentials"}
        async with session.post(SPOTIFY_TOKEN_URL, headers=headers, data=data) as r:
            if r.status != 200:
                raise RuntimeError(f"Spotify token fetch failed: {r.status}")
            body = await r.json()
            self._token = body["access_token"]
            self._expires = time.monotonic() + body["expires_in"] - 30

    async def _get(self, path: str) -> dict:
        await self._ensure_token()
        session = await self._get_session()
        headers = {"Authorization": f"Bearer {self._token}"}
        async with session.get(f"{SPOTIFY_API_BASE}{path}", headers=headers) as r:
            if r.status != 200:
                raise RuntimeError(f"Spotify API error {r.status}: {path}")
            return await r.json()

    async def get_track(self, track_id: str) -> List[str]:
        data = await self._get(f"/tracks/{track_id}")
        name = data["name"]
        artists = ", ".join(a["name"] for a in data["artists"])
        return [f"{artists} - {name}"]

    async def get_album_tracks(self, album_id: str) -> List[str]:
        album_data = await self._get(f"/albums/{album_id}")
        album_artists = ", ".join(a["name"] for a in album_data.get("artists", []))
        tracks = []
        offset = 0
        while len(tracks) < SPOTIFY_PLAYLIST_CAP:
            page = await self._get(
                f"/albums/{album_id}/tracks?limit=50&offset={offset}"
            )
            items = page.get("items", [])
            if not items:
                break
            for item in items:
                artist = ", ".join(a["name"] for a in item.get("artists", [])) or album_artists
                tracks.append(f"{artist} - {item['name']}")
            if len(items) < 50:
                break
            offset += len(items)
        return tracks[:SPOTIFY_PLAYLIST_CAP]

    async def get_artist_top_tracks(self, artist_id: str, market: str = "US") -> List[str]:
        data = await self._get(f"/artists/{artist_id}/top-tracks?market={market}")
        tracks = []
        for t in data.get("tracks", []):
            artists = ", ".join(a["name"] for a in t.get("artists", []))
            tracks.append(f"{artists} - {t['name']}")
        return tracks[:SPOTIFY_PLAYLIST_CAP]

    async def get_playlist_tracks(self, playlist_id: str) -> List[str]:
        data = await self._get(
            f"/playlists/{playlist_id}"
            f"?fields=tracks.items(track(name,artists(name)))"
        )
        tracks = []
        for item in data.get("tracks", {}).get("items", [])[:SPOTIFY_PLAYLIST_CAP]:
            t = item.get("track")
            if not t:
                continue
            name = t.get("name", "")
            artists = ", ".join(a["name"] for a in t.get("artists", []))
            if name and artists:
                tracks.append(f"{artists} - {name}")
            elif name:
                tracks.append(name)
        return tracks

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

_spotify = SpotifyClient()

# ── MUSIC CARD GENERATOR ─────────────────────────────────────

async def _gen_music_card(
    title: str,
    author: str,
    artwork_url: str,
    duration: str,
    progress: float = 0.15,
    position_str: str = "",
    session: Optional[aiohttp.ClientSession] = None,
) -> discord.File:
    close_session = False
    if session is None:
        session = aiohttp.ClientSession()
        close_session = True

    try:
        async with session.get(artwork_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Failed to fetch artwork: {resp.status}")
            img_bytes = await resp.read()
    except Exception as e:
        log.warning(f"Artwork fetch failed: {e}, using fallback")
        fallback = Image.new("RGB", (320, 320), (40, 40, 50))
        buf = io.BytesIO()
        fallback.save(buf, format="PNG")
        img_bytes = buf.getvalue()
    finally:
        if close_session:
            await session.close()

    def _process_image(img_bytes: bytes) -> io.BytesIO:
        W, H = 1280, 400

        title_font = ImageFont.truetype("arialbd.ttf", 44)
        subtitle_font = ImageFont.truetype("arial.ttf", 28)
        dur_font = ImageFont.truetype("arial.ttf", 24)

        art_orig = Image.open(io.BytesIO(img_bytes)).convert("RGB")

        bg = art_orig.resize((W, H), Image.Resampling.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(40))
        bg = Image.blend(bg, Image.new("RGB", (W, H), (20, 20, 25)), 0.7)

        grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        grad_draw = ImageDraw.Draw(grad)
        for y in range(H):
            grad_draw.line([(0, y), (W, y)], fill=(0, 0, 0, int(80 * (y / H))))
        bg = Image.alpha_composite(bg.convert("RGBA"), grad)

        art_size = 320
        art2 = art_orig.resize((art_size, art_size)).convert("RGBA")
        mask = Image.new("L", (art_size, art_size), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, art_size, art_size), radius=25, fill=255)
        art2.putalpha(mask)
        art_x = W - art_size - 50
        art_y = (H - art_size) // 2
        bg.paste(art2, (art_x, art_y), art2)

        card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        card_draw = ImageDraw.Draw(card)
        gl, gt, gr, gb = 40, 40, art_x - 30, H - 40
        card_draw.rounded_rectangle([gl, gt, gr, gb], radius=35, fill=(255, 255, 255, 15))
        card_draw.rounded_rectangle([gl, gt, gr, gb], radius=35, outline=(255, 255, 255, 40), width=1)
        bg = Image.alpha_composite(bg, card)
        draw = ImageDraw.Draw(bg)

        max_w = gr - 80 - 40

        def trunc(text: str, font) -> str:
            if draw.textbbox((0, 0), text, font=font)[2] <= max_w:
                return text
            while text:
                if draw.textbbox((0, 0), text + "...", font=font)[2] <= max_w:
                    return text + "..."
                text = text[:-1]
            return "..."

        draw.text((80, 90), trunc(title, title_font), font=title_font, fill=(255, 255, 255, 250))
        draw.text((80, 150), trunc(author, subtitle_font), font=subtitle_font, fill=(255, 255, 255, 180))
        draw.text((80, 280), f"{position_str} / {duration}" if position_str else f"Duration: {duration}", font=dur_font, fill=(255, 255, 255, 160))

        bx, by, bw, bh = 80, 330, max_w, 4
        draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=2, fill=(255, 255, 255, 50))
        fw = int(bw * max(0.0, min(1.0, progress)))
        draw.rounded_rectangle([bx, by, bx + fw, by + bh], radius=2, fill=(255, 255, 255, 200))
        dx, dy, dr = bx + fw, by + bh // 2, 8
        draw.ellipse([dx - dr + 1, dy - dr + 1, dx + dr + 1, dy + dr + 1], fill=(0, 0, 0, 60))
        draw.ellipse([dx - dr, dy - dr, dx + dr, dy + dr], fill=(255, 255, 255, 255))

        buf = io.BytesIO()
        bg.convert("RGB").save(buf, format="PNG", quality=95)
        buf.seek(0)
        return buf

    processed_buf = await asyncio.to_thread(_process_image, img_bytes)
    return discord.File(fp=processed_buf, filename="neixomusiccard.png")

# ── LRC PARSER ───────────────────────────────────────────────

_LRC_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\]\s*(.*)")

def _parse_lrc(text: str) -> Optional[List[Tuple[int, str]]]:
    lines = text.strip().splitlines()
    parsed = []
    total = 0
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        total += 1
        m = _LRC_RE.match(raw)
        if m:
            mins, secs, frac, txt = m.groups()
            ms = int(mins) * 60000 + int(secs) * 1000
            if frac:
                ms += int(frac.ljust(3, "0")[:3])
            parsed.append((ms, txt.strip()))
    if total == 0 or len(parsed) / total < 0.7:
        return None
    parsed.sort(key=lambda x: x[0])
    return parsed
