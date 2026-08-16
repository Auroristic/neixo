from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
from collections import deque
from typing import cast

import aiohttp
import discord
import syncedlyrics
import wavelink
from discord.ext import commands
from discord.ui import Select, View

from cogs.music_helpers import (
    SEARCH_RETRIES,
    SEARCH_RETRY_DELAY,
    SOUNDCLOUD_RE,
    SPOTIFY_ALBUM_RE,
    SPOTIFY_BATCH_SIZE,
    SPOTIFY_PLAYLIST_CAP,
    SPOTIFY_PLAYLIST_RE,
    SPOTIFY_TRACK_RE,
    _err_embed,
    _fmt_time,
    _gen_music_card,
    _is_bandcamp_url,
    _is_spotify_url,
    _is_track_allowed,
    _ok_embed,
    _parse_lrc,
    _scrape_spotify_playlist,
    _search_bandcamp,
    _spotify,
)
from cogs.music_views import (
    GenreView,
    LoopView,
    LyricsPaginationView,
    NowPlayingView,
    QueueView,
    SCRetryView,
    SimilarView,
)
from neixoconfig import Neixocolor, Neixoemojis
from utils import help_meta, is_owner_or_creator

# ── cogs/music.py ───────────────────────────────────────────────
MUSIC_LOCKED = False

COG_META = {
    "category": "music",
    "label": "Music",
    "desc": "Music playback, queue controls, and audio filters.",
    "owner": False,
}



log = logging.getLogger(__name__)

PLAYBACK_RETRY_LIMIT = 2
PLAYBACK_RETRY_BASE_DELAY = 1.5
_RETRYABLE_PLAYBACK_ERROR_MARKERS = (
    "allclientsfailedexception",
    "all clients failed",
    "read timed out",
    "sign in to confirm",
    "not a bot",
    "requires login",
    "video requires login",
    "video player configuration error",
    "configuration error",
)


def _playback_error_text(error: object) -> str:
    if error is None:
        return ""
    if isinstance(error, dict):
        try:
            return json.dumps(error, ensure_ascii=False)
        except TypeError:
            return str(error)
    return str(error)


def _is_retryable_playback_error(error: object) -> bool:
    text = _playback_error_text(error).lower()
    return any(marker in text for marker in _RETRYABLE_PLAYBACK_ERROR_MARKERS)


def _is_failed_track_end_reason(reason: object) -> bool:
    return str(reason).lower() in {"loadfailed", "load_failed"}


def _track_retry_key(track: object) -> str:
    return (
        getattr(track, "uri", None)
        or getattr(track, "identifier", None)
        or f"{getattr(track, 'title', '')}|{getattr(track, 'author', '')}"
    )


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._history: dict[int, deque[wavelink.Playable]] = {}
        self._np_views: dict[int, NowPlayingView] = {}
        self._track_locks: dict[int, asyncio.Lock] = {}
        self._http_session: aiohttp.ClientSession | None = None
        self._live_tasks: dict[int, asyncio.Task] = {}
        self._live_msgs: dict[int, discord.Message] = {}
        # Guilds where the ⏮ (prev) button was just pressed. Set before the
        # replace-style player.play() in prev_btn, cleared in on_track_end so
        # the queue auto-advance for the *replaced* track is suppressed.
        self._prev_pressed: set[int] = set()
        self._session_stats: dict[int, dict] = {}
        self._disconnecting: set[int] = set()
        self._playback_retries: dict[int, dict[str, int]] = {}
        self._pending_playback_retries: dict[int, str] = {}

    async def cog_check(self, ctx: commands.Context) -> bool:
        """Music is discontinued — only owner/creator/server-owner can still test it."""
        if ctx.guild is None:
            await ctx.send("this command only works in servers.")
            return False
        if MUSIC_LOCKED and not is_owner_or_creator(ctx):
            await ctx.send(
                "music is discontinued, sorry. maybe one day it comes back, who knows."
            )
            return False
        return True

    async def cog_load(self) -> None:
        """Initialize HTTP session when cog loads"""
        self._http_session = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        await _spotify.close()
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
        # cancel live lyrics loops and drop stale views so a reload doesn't leak them
        for task in list(self._live_tasks.values()):
            task.cancel()
        self._live_tasks.clear()
        self._live_msgs.clear()
        self._np_views.clear()

    def _clean_query(self, text: str) -> str:
        text = re.sub(r'\s*\([^)]*\)', '', text)
        text = re.sub(r'\s*\[[^\]]*\]', '', text)
        text = re.sub(r'\s*(ft\.|feat\.|featuring)\s+[^,]+', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _get_history(self, guild_id: int) -> deque[wavelink.Playable]:
        if guild_id not in self._history:
            self._history[guild_id] = deque(maxlen=50)
        return self._history[guild_id]

    async def _fetch_lyrics(self, artist: str, title: str) -> tuple[str, list[tuple[int, str]] | None] | None:
        """Returns (plain_text, synced_lines) or None. synced_lines is list of (ms, text) or None."""
        clean_artist = self._clean_query(artist)
        clean_title = self._clean_query(title)
        clean_artist = re.sub(r'\s*-\s*Topic$', '', clean_artist, flags=re.IGNORECASE).strip()

        # YouTube titles often embed the real artist: "Artist - Song" or
        # "Song - Artist" or "Song | Artist" or "Song by Artist".
        # track.author from YT is often just the uploader (channel name),
        # which is useless for lyrics search. If the title contains a
        # separator, split it and use the parts as artist/title directly.
        sep_match = re.match(r'^(.+?)\s*[-:|–—]+\s+(.+)$', clean_title)
        if sep_match:
            # "Artist - Title" pattern — left side is likely the artist
            clean_artist = self._clean_query(sep_match.group(1))
            clean_title = self._clean_query(sep_match.group(2))
        elif clean_artist:
            # Handle trailing "Title by Artist"
            by_match = re.match(r'^(.+?)\s+by\s+' + re.escape(clean_artist) + r'\s*$', clean_title, re.IGNORECASE)
            if by_match:
                clean_title = by_match.group(1).strip()

        # 1. syncedlyrics (Musixmatch + NetEase + Genius) — try synced first
        try:
            raw = await self._syncedlyrics_search(clean_artist, clean_title)
            if raw:
                synced = _parse_lrc(raw)
                if synced:
                    plain = "\n".join(t for _, t in synced if t)
                    return (plain, synced)
                return (raw, None)
        except Exception as e:
            log.warning("syncedlyrics failed: %s", e)

        # 2. LRCLIB with artist
        try:
            result = await self._lrclib_lyrics(clean_artist, clean_title)
            if result:
                return result
        except Exception as e:
            log.warning("LRCLIB lyrics failed: %s", e)

        # 2b. LRCLIB title-only fallback
        try:
            result = await self._lrclib_lyrics("", clean_title)
            if result:
                return result
        except Exception as e:
            log.warning("LRCLIB title-only failed: %s", e)

        # 3. OVH (plain only)
        try:
            lyrics = await self._ovh_lyrics(clean_artist, clean_title)
            if lyrics:
                return (lyrics, None)
        except Exception as e:
            log.warning("lyrics.ovh failed: %s", e)

        return None

    async def _syncedlyrics_search(self, artist: str, title: str) -> str | None:
        query = f"{title} {artist}"
        def sync_search():
            try:
                return syncedlyrics.search(query)
            except Exception:
                return None
        return await asyncio.get_running_loop().run_in_executor(None, sync_search)

    async def _lrclib_lyrics(self, artist: str, title: str) -> tuple[str, list[tuple[int, str]] | None] | None:
        url = f"https://lrclib.net/api/get?artist_name={urllib.parse.quote(artist)}&track_name={urllib.parse.quote(title)}"
        session = await self._get_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                synced_raw = data.get("syncedLyrics")
                plain_raw = data.get("plainLyrics")
                if synced_raw:
                    synced = _parse_lrc(synced_raw)
                    if synced:
                        plain = plain_raw or "\n".join(t for _, t in synced if t)
                        return (plain, synced)
                if plain_raw:
                    return (plain_raw, None)
                return None
        except Exception:
            return None

    async def _ovh_lyrics(self, artist: str, title: str) -> str | None:
        url = f"https://api.lyrics.ovh/v1/{urllib.parse.quote(artist)}/{urllib.parse.quote(title)}"
        session = await self._get_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                raw = data.get("lyrics", "")
                if not raw:
                    return None
                raw = raw.replace("\\n", "\n").replace("\\r", "")
                return raw
        except Exception:
            return None

    async def _schedule_lyrics_fetch(
        self,
        player: wavelink.Player,
        track: wavelink.Playable,
        view: NowPlayingView,
    ) -> None:
        await asyncio.sleep(5)
        if player.current != track:
            return
        guild_id = player.guild.id
        if self._np_views.get(guild_id) is not view:
            return
        artist = track.author
        title = track.title
        result = await self._fetch_lyrics(artist, title)
        if not result:
            return
        if self._np_views.get(guild_id) is not view or not view.message:
            return
        plain, synced = result
        view._lyrics_data = (artist, title, plain)
        view._synced_lines = synced
        view.lyrics_btn.disabled = False
        if synced:
            view.lyrics_btn.emoji = "<a:emoji_44:1253070278259642521>"
        try:
            await view.message.edit(view=view)
        except discord.HTTPException:
            pass

    def _cancel_live_lyrics(self, guild_id: int) -> None:
        task = self._live_tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()

    def _render_live_window(self, lines: list[tuple[int, str]], idx: int, double: bool) -> str:
        """Render 3 before + current (+ next if double) + 3 after. Current lines get # heading."""
        total = len(lines)
        # Determine which indices are "active" (bolded with #)
        active = {idx}
        if double and idx + 1 < total:
            active.add(idx + 1)
        # Window: 3 before first active, 3 after last active
        first_active = min(active)
        last_active = max(active)
        start = max(0, first_active - 3)
        end = min(total, last_active + 4)
        out = []
        for i in range(start, end):
            txt = lines[i][1] if lines[i][1] else "♪ ..."
            if i in active:
                out.append(f"# {txt}")
            else:
                out.append(txt)
        return "\n".join(out)

    async def _start_live_lyrics(self, channel: discord.TextChannel, player: wavelink.Player, synced: list[tuple[int, str]], track: wavelink.Playable) -> None:
        guild_id = player.guild.id
        self._cancel_live_lyrics(guild_id)
        # Delete old live msg if exists
        old_msg = self._live_msgs.pop(guild_id, None)
        if old_msg:
            try:
                await old_msg.delete()
            except discord.HTTPException:
                pass
        # Send initial message
        msg = await channel.send("♪ ...")
        self._live_msgs[guild_id] = msg
        task = asyncio.create_task(self._live_lyrics_loop(player, track, synced, msg, guild_id))
        self._live_tasks[guild_id] = task

    async def _live_lyrics_loop(self, player: wavelink.Player, track: wavelink.Playable, synced: list[tuple[int, str]], msg: discord.Message, guild_id: int) -> None:
        last_state = None
        try:
            _active_speed = 1.0
            try:
                _ts_init = player.filters.timescale.payload or {}
                _active_speed = (_ts_init.get('speed') or 1.0) * (_ts_init.get('rate') or 1.0)
            except Exception:
                pass
            _anchor_pos = int(player.position)
            while True:
                await asyncio.sleep(1.25)
                if not player.connected or player.current != track:
                    break
                if player.paused:
                    continue
                raw_pos = int(player.position)

                # Read current speed from filters
                _speed = 1.0
                try:
                    _ts = player.filters.timescale.payload or {}
                    _speed = (_ts.get('speed') or 1.0) * (_ts.get('rate') or 1.0)
                except Exception:
                    pass

                # Speed changed or position jumped (seek) — reset anchor
                if _speed != _active_speed or (raw_pos < _anchor_pos):
                    _active_speed = _speed
                    _anchor_pos = raw_pos

                # Apply correction
                if _active_speed != 1.0:
                    elapsed = raw_pos - _anchor_pos
                    pos = int(_anchor_pos + elapsed * _active_speed)
                else:
                    pos = raw_pos
                # Find current line index
                idx = -1
                for i, (ms, _) in enumerate(synced):
                    if ms <= pos:
                        idx = i
                    else:
                        break
                if idx < 0:
                    # Before first lyric
                    content = "♪ ..."
                    state = -1
                elif idx >= len(synced) - 1:
                    # Last line or past it
                    content = self._render_live_window(synced, idx, False) + "\n♪ ..."
                    state = idx
                else:
                    # Double-bold next line if current line is short (< 1.5s)
                    # to avoid an extra edit when it ends quickly.
                    duration = synced[idx + 1][0] - synced[idx][0]
                    double = duration < 1500
                    content = self._render_live_window(synced, idx, double)
                    state = idx
                if state == last_state:
                    continue
                last_state = state
                try:
                    await msg.edit(content=content)
                except discord.NotFound:
                    break
                except discord.HTTPException:
                    break
            # Song ended naturally — show final state
            if player.current != track and last_state and last_state >= 0:
                try:
                    final = self._render_live_window(synced, len(synced) - 1, False) + "\n♪ ..."
                    await msg.edit(content=final)
                except discord.HTTPException:
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            # only pop if this is still the registered task — a restarted
            # lyrics loop may have replaced us under the same key
            if self._live_tasks.get(guild_id) is asyncio.current_task():
                self._live_tasks.pop(guild_id, None)

    async def _check_vc(self, ctx: commands.Context) -> bool:
        if not ctx.guild:
            return False
        bot_vc = ctx.guild.voice_client
        user_vc = ctx.author.voice.channel if ctx.author.voice else None
        if bot_vc:
            if not user_vc or bot_vc.channel != user_vc:
                await ctx.send(embed=_err_embed(f"you need to be in {bot_vc.channel.mention} to use this.", ctx))
                return False
        else:
            if not user_vc:
                await ctx.send(embed=_err_embed("join a voice channel first.", ctx))
                return False
            if not user_vc.permissions_for(ctx.guild.me).connect:
                await ctx.send(embed=_err_embed("i need `connect` permission in that voice channel.", ctx))
                return False
            if not user_vc.permissions_for(ctx.guild.me).speak:
                await ctx.send(embed=_err_embed("i need `speak` permission in that voice channel.", ctx))
                return False
        return True

    async def _check_playing(self, ctx: commands.Context) -> bool:
        if not ctx.guild:
            return False
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        if not player or not player.playing:
            await ctx.send(embed=_err_embed("nothing is playing rn.", ctx))
            return False
        return True

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    async def _update_vc_status(self, channel_id: int, status: str) -> None:
        url = f"https://discord.com/api/v9/channels/{channel_id}/voice-status"
        headers = {"Authorization": f"Bot {self.bot.http.token}", "Content-Type": "application/json"}
        try:
            session = await self._get_session()
            async with session.put(url, json={"status": status}, headers=headers) as r:
                if r.status not in (200, 204):
                    log.warning("vc status update failed: %s", r.status)
        except Exception as e:
            log.warning("vc status error: %s", e)

    async def _fetch_similar(self, vid_id: str, cap: int = 7) -> list:
        if not vid_id:
            return []
        url = f"https://www.youtube.com/watch?v={vid_id}&list=RD{vid_id}"
        headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}
        session = await self._get_session()
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return []
                text = await resp.text()
        except Exception:
            return []
        match = re.search(r"ytInitialData\s*=\s*(\{.*?\});", text, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(1))
            items = (
                data.get("contents", {})
                .get("twoColumnWatchNextResults", {})
                .get("playlist", {})
                .get("playlist", {})
                .get("contents", [])
            )
        except Exception:
            return []
        tracks = []
        for item in items:
            if len(tracks) >= cap:
                break
            v = item.get("playlistPanelVideoRenderer")
            if not v:
                continue
            vid = v.get("videoId")
            if not vid or vid == vid_id:
                continue
            try:
                title = v.get("title", {}).get("runs", [{}])[0].get("text")
            except Exception:
                title = None
            if not title:
                title = v.get("title", {}).get("simpleText", "Unknown")
            tracks.append({"title": title, "identifier": vid, "url": f"https://www.youtube.com/watch?v={vid}"})
        return tracks

    async def _resolve_spotify(self, query: str) -> list[str]:
        if m := SPOTIFY_TRACK_RE.search(query):
            return await _spotify.get_track(m.group(1))
        if m := SPOTIFY_ALBUM_RE.search(query):
            return await _spotify.get_album_tracks(m.group(1))
        if m := SPOTIFY_PLAYLIST_RE.search(query):
            tracks = await _scrape_spotify_playlist(query)
            return tracks[:SPOTIFY_PLAYLIST_CAP]
        return []

    async def _yt_search_with_retry(self, query: str, source: str = "ytsearch"):
        """wavelink search with retries on transient errors. Returns list/Playlist or [] on failure."""
        last_err: Exception | None = None
        for attempt in range(SEARCH_RETRIES + 1):
            try:
                results = await wavelink.Playable.search(query, source=source)
                if results:
                    return results
                # empty result — no point retrying with same query
                return results
            except Exception as e:
                last_err = e
                log.warning("search attempt %d for %r failed: %s", attempt + 1, query, e)
                if attempt < SEARCH_RETRIES:
                    await asyncio.sleep(SEARCH_RETRY_DELAY * (attempt + 1))
        if last_err:
            log.warning("all search attempts failed for %r: %s", query, last_err)
        return []

    async def _search_with_fallback(self, query: str):
        if query.startswith(("http://", "https://", "scsearch:", "ytsearch:", "ytmsearch:", "spsearch:")):
            results = await self._yt_search_with_retry(query)
            if results:
                return results

        results = await self._yt_search_with_retry(query, source="ytmsearch")
        if results:
            return results
        results = await self._yt_search_with_retry(query + " audio", source="ytsearch")
        if results:
            return results
        results = await self._yt_search_with_retry(query, source="ytsearch")
        if results:
            return results
        return await self._yt_search_with_retry(query, source="scsearch")

    _VIDEO_KEYWORDS_RE = re.compile(
        r"\b(official\s*(music\s*)?video|music\s*video|official\s*mv|"
        r"official\s*visualizer|"
        r"\bmv\b|\bm/v\b|\(video\)|\[video\])",
        re.IGNORECASE,
    )

    def _prefer_audio_track(self, tracks: list, query: str = "") -> wavelink.Playable:
        """From a list of search results, prefer tracks without 'Official Music Video' etc.
        If a query is given, also prefer tracks whose title contains the query words."""
        # First pass: filter out music videos
        non_video = [t for t in tracks if not self._VIDEO_KEYWORDS_RE.search(t.title)]
        candidates = non_video or tracks

        # Second pass: if query given, prefer results that don't have
        # "slowed", "sped up", "reverb" etc. unless the user asked for it
        if query:
            q_lower = query.lower()
            if "slowed" not in q_lower and "sped" not in q_lower and "reverb" not in q_lower:
                clean = [t for t in candidates if not re.search(r'\b(slowed|sped\s*up|reverb)\b', t.title, re.IGNORECASE)]
                if clean:
                    candidates = clean

        return candidates[0]

    async def _send_now_playing(
        self,
        channel: discord.TextChannel,
        player: wavelink.Player,
        track: wavelink.Playable,
    ) -> None:
        guild_id = player.guild.id

        if guild_id not in self._track_locks:
            self._track_locks[guild_id] = asyncio.Lock()

        async with self._track_locks[guild_id]:
            old_view = self._np_views.pop(guild_id, None)
            if player.current and player.current != track:
                return
            view = NowPlayingView(self, player)
            self._np_views[guild_id] = view

        if old_view:
            await old_view.deactivate()
            if old_view.message:
                try:
                    await old_view.message.delete()
                except discord.HTTPException:
                    pass

        placeholder = discord.Embed(
            description=f"**{track.title}** by {track.author}\n-# loading card...",
            color=Neixocolor,
        )
        msg = await channel.send(embed=placeholder, view=view)
        view.message = msg

        try:
            duration = _fmt_time(track.length)
            card_file = await _gen_music_card(
                track.title,
                track.author,
                track.artwork,
                duration,
                session=self._http_session
            )
            card_embed = discord.Embed(
                description=f"-# **[{track.title}]({track.uri})** by {track.author}",
                color=Neixocolor,
            )
            card_embed.set_image(url="attachment://neixomusiccard.png")
            if self._np_views.get(guild_id) is view and view.message:
                await view.message.edit(embed=card_embed, attachments=[card_file], view=view)
        except Exception as e:
            log.warning("_send_now_playing card gen failed: %s", e)


    async def _connect_player(self, ctx: commands.Context) -> wavelink.Player | None:
        """Get or create the wavelink player for this guild. Returns None (with error sent) on failure."""
        vc = ctx.voice_client
        if isinstance(vc, discord.VoiceClient) and not isinstance(vc, wavelink.Player):
            await ctx.send(embed=_err_embed("something else is using the vc. use `.disconnect` first.", ctx))
            return None

        player: wavelink.Player = cast(wavelink.Player, vc)
        if not player:
            try:
                channel = ctx.author.voice.channel
                player = await channel.connect(cls=wavelink.Player)
                max_bitrate = getattr(ctx.guild, "bitrate_limit", 96000)
                try:
                    await channel.edit(bitrate=max_bitrate)
                except discord.Forbidden:
                    log.warning("no permission to edit channel bitrate")
            except (AttributeError, discord.ClientException, wavelink.ChannelTimeoutException):
                return None

        player.autoplay = wavelink.AutoPlayMode.disabled
        if not hasattr(player, "home"):
            player.home = ctx.channel
        return player

    async def _play_sc_core(self, ctx: commands.Context, query: str) -> None:
        """SoundCloud-first playback. Used by `.playsc` and as a YouTube fallback."""
        if not await self._check_vc(ctx):
            return

        player = await self._connect_player(ctx)
        if player is None:
            return

        tracks = await self._yt_search_with_retry(query, source="scsearch")
        if not tracks:
            return await ctx.send(
                embed=_err_embed(
                    "couldn't find anything on SoundCloud. try `.play <query>` for YouTube or a Spotify link.",
                    ctx,
                )
            )
        await self._queue_tracks(ctx, player, tracks, source_label="SoundCloud")

    async def _play_bandcamp_core(
        self,
        ctx: commands.Context,
        query: str,
        player: wavelink.Player | None = None,
    ) -> None:
        """Bandcamp playback for direct URLs and best-effort text search."""
        if player is None:
            if not await self._check_vc(ctx):
                return
            player = await self._connect_player(ctx)
            if player is None:
                return

        tracks = await self._resolve_bandcamp(query)
        if not tracks:
            return await ctx.send(
                embed=_err_embed(
                    "couldn't find anything on Bandcamp. try a Bandcamp link or `.playbc <query>`.",
                    ctx,
                )
            )

        await self._queue_tracks(ctx, player, tracks, source_label="Bandcamp")

    async def _resolve_bandcamp(self, query: str):
        target = query.strip() if _is_bandcamp_url(query) else await _search_bandcamp(query)
        if not target:
            return None
        return await self._yt_search_with_retry(target, source="bandcamp")

    async def _play_spotify_core(
        self,
        ctx: commands.Context,
        query: str,
        player: wavelink.Player | None = None,
    ) -> None:
        """Spotify playback for direct URLs via Lavalink's spotify source (LavaSrc mirror)."""
        if player is None:
            if not await self._check_vc(ctx):
                return
            player = await self._connect_player(ctx)
            if player is None:
                return

        tracks = await self._resolve_spotify_direct(query)
        if not tracks:
            return await ctx.send(
                embed=_err_embed(
                    "couldn't find anything on Spotify. try a Spotify link or `.playsc <query>`.",
                    ctx,
                )
            )

        await self._queue_tracks(ctx, player, tracks, source_label="Spotify")

    async def _resolve_spotify_direct(self, query: str):
        return await self._yt_search_with_retry(query.strip(), source="spotify")

    async def _play_core(self, ctx: commands.Context, query: str) -> None:
        if not await self._check_vc(ctx):
            return

        player = await self._connect_player(ctx)
        if player is None:
            return

        if _is_bandcamp_url(query.strip()):
            return await self._play_bandcamp_core(ctx, query, player=player)

        if _is_spotify_url(query.strip()):
            return await self._play_spotify_core(ctx, query, player=player)

        is_spotify = (
            "spotify.com" in query
            and (
                SPOTIFY_TRACK_RE.search(query)
                or SPOTIFY_PLAYLIST_RE.search(query)
                or SPOTIFY_ALBUM_RE.search(query)
            )
        )
        if is_spotify:
            try:
                names = await self._resolve_spotify(query)
            except Exception as e:
                log.warning("Spotify resolve error: %s", e)
                return await ctx.send(embed=_err_embed("couldn't reach Spotify API.", ctx))

            if not names:
                return await ctx.send(embed=_err_embed("no tracks found on Spotify.", ctx))

            capped = len(names) < SPOTIFY_PLAYLIST_CAP
            status_msg = await ctx.send(
                embed=_ok_embed(f"loading {len(names)} Spotify track(s)...", ctx)
            )

            sem = asyncio.Semaphore(SPOTIFY_BATCH_SIZE)

            async def _resolve_one(name: str):
                async with sem:
                    results = await self._yt_search_with_retry(name, source="ytmsearch")
                    if not results:
                        results = await self._yt_search_with_retry(name + " audio", source="ytsearch")
                    if not results:
                        results = await self._yt_search_with_retry(name, source="ytsearch")
                if not results:
                    return None
                # results is list[Playable] for ytmsearch/ytsearch
                if isinstance(results, list) and len(results) > 1:
                    track = self._prefer_audio_track(results, name)
                else:
                    track = results[0] if isinstance(results, list) else None
                if not track:
                    return None
                ok, _reason = _is_track_allowed(track)
                return track if ok else None

            added = 0
            rejected = 0
            chunk_size = 20  # batch progress updates and start playback early
            for chunk_start in range(0, len(names), chunk_size):
                # Ensure we have a live player before queueing this chunk
                player = await self._ensure_player_connected(player, ctx)
                if player is None:
                    break
                chunk = names[chunk_start : chunk_start + chunk_size]
                resolved = await asyncio.gather(
                    *[_resolve_one(n) for n in chunk], return_exceptions=False
                )
                for t in resolved:
                    if t is None:
                        rejected += 1
                        continue
                    await player.queue.put_wait(t)
                    added += 1

                # Start playback as soon as we have something
                if added and not player.playing and not player.queue.is_empty:
                    try:
                        await player.play(player.queue.get())
                    except Exception as e:
                        log.warning("Spotify initial play failed: %s", e)

                if chunk_start + chunk_size < len(names):
                    try:
                        await status_msg.edit(
                            embed=_ok_embed(f"loading... {added}/{len(names)} tracks queued", ctx)
                        )
                    except discord.HTTPException:
                        pass

            suffix = "" if capped else f" (capped at {SPOTIFY_PLAYLIST_CAP})"
            extra = f" {rejected} skipped (too long / live / unavailable)." if rejected else ""
            await status_msg.edit(
                embed=_ok_embed(f"queued **{added}** Spotify track(s){suffix}.{extra}", ctx)
            )
            return

        if SOUNDCLOUD_RE.search(query):
            tracks = await self._yt_search_with_retry(query, source="scsearch")
            if not tracks:
                return await ctx.send(
                    embed=_err_embed("couldn't find anything on SoundCloud. try `.playsc <query>`.", ctx)
                )
            await self._queue_tracks(ctx, player, tracks, source_label="SoundCloud")
            return

        tracks = await self._yt_search_with_retry(query, source="ytmsearch")
        if not tracks:
            tracks = await self._yt_search_with_retry(query + " audio", source="ytsearch")
        if not tracks:
            tracks = await self._yt_search_with_retry(query, source="ytsearch")
        if not tracks:
            view = SCRetryView(self, ctx, query)
            await ctx.send(
                embed=_err_embed(
                    "couldn't find anything on YouTube. "
                    "try `.playsc <query>` (SoundCloud) or `.playbc <query>` (Bandcamp).",
                    ctx,
                ),
                view=view,
            )
            return

        if isinstance(tracks, list) and len(tracks) > 1:
            tracks = [self._prefer_audio_track(tracks, query)]

        await self._queue_tracks(ctx, player, tracks)

    async def _queue_tracks(
        self,
        ctx: commands.Context,
        player: wavelink.Player,
        tracks,
        *,
        source_label: str = "",
        send=None,
    ) -> None:
        sender = send or ctx.send
        src = f" [{source_label}]" if source_label else ""
        if isinstance(tracks, wavelink.Playlist):
            added = 0
            rejected = 0
            for t in tracks.tracks:
                ok, _reason = _is_track_allowed(t)
                if not ok:
                    rejected += 1
                    continue
                await player.queue.put_wait(t)
                added += 1
            if added == 0:
                return await sender(
                    embed=_err_embed(f"every track in **{tracks.name}** was filtered out (too long / live).", ctx)
                )
            extra = f" {rejected} skipped (too long or live)." if rejected else ""
            await sender(
                embed=_ok_embed(f"added playlist **{tracks.name}** ({added} songs){src}.{extra}", ctx)
            )
        else:
            track = tracks[0]
            ok, reason = _is_track_allowed(track)
            if not ok:
                return await sender(embed=_err_embed(reason, ctx))
            pos = player.queue.count + 1
            await player.queue.put_wait(track)
            ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(pos, f"{pos}th")
            await sender(
                embed=_ok_embed(f"added **{track.title}** to {ordinal} in the queue{src}.", ctx)
            )

        # Serialize playback start through the per-guild lock so we don't race
        # with a concurrent _queue_tracks call or with on_wavelink_track_end.
        guild_id = player.guild.id
        if guild_id not in self._track_locks:
            self._track_locks[guild_id] = asyncio.Lock()
        async with self._track_locks[guild_id]:
            if not player.playing and not player.queue.is_empty:
                player = await self._ensure_player_connected(player, ctx)
                if player is None:
                    return
                try:
                    await player.play(player.queue.get())
                except Exception as e:
                    log.warning("failed to start playback: %s", e)

    async def _ensure_player_connected(self, player: wavelink.Player, ctx: commands.Context) -> wavelink.Player | None:
        """Check if the node is connected; reconnect the voice client if not. Returns the (possibly new) player or None on failure."""
        if getattr(player, "connected", False):
            return player
        try:
            saved_queue = list(player.queue)
            vc = ctx.voice_client
            if vc:
                await vc.disconnect(force=True)
            channel = ctx.author.voice.channel
            player = await channel.connect(cls=wavelink.Player)
            player.home = ctx.channel
            player.autoplay = wavelink.AutoPlayMode.disabled
            for t in saved_queue:
                await player.queue.put_wait(t)
            return player
        except Exception as e:
            log.warning("failed to reconnect stale player: %s", e)
            return None

    async def _apply_filter(
        self,
        ctx: commands.Context,
        filters: wavelink.Filters,
        label: str,
        vol_cap: int | None = None,
    ) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        if vol_cap is not None:
            await player.set_volume(min(player.volume, vol_cap))
        await player.set_filters(filters)
        await ctx.send(embed=_ok_embed(f"{label} on.", ctx))
        # Seek back 5s and restart live lyrics below the command
        if player.current:
            seek_to = max(0, int(player.position) - 5000)
            await player.seek(seek_to)
            guild_id = player.guild.id
            if guild_id in self._live_tasks:
                view = self._np_views.get(guild_id)
                synced = view._synced_lines if view else None
                if synced:
                    await self._start_live_lyrics(ctx.channel, player, synced, player.current)

    # ── wavelink events ───────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        # Auto-disconnect when VC becomes empty
        if member.id != self.bot.user.id and before.channel is not None:
            guild = member.guild
            vc = guild.voice_client
            if vc and vc.channel == before.channel:
                humans = [m for m in before.channel.members if not m.bot]
                if not humans:
                    guild_id = guild.id
                    if guild_id in self._disconnecting:
                        return
                    self._disconnecting.add(guild_id)
                    try:
                        await asyncio.sleep(15)  # wait 15 seconds
                        # re-check after sleep — someone may have rejoined
                        vc = guild.voice_client
                        if vc and vc.channel == before.channel:
                            humans = [m for m in before.channel.members if not m.bot]
                            if not humans:
                                self._cancel_live_lyrics(guild_id)
                                player: wavelink.Player = cast(wavelink.Player, vc)
                                np_view = self._np_views.pop(guild_id, None)
                                if np_view:
                                    await np_view.deactivate()
                                    if np_view.message:
                                        try:
                                            await np_view.message.delete()
                                        except discord.HTTPException:
                                            pass
                                self._history.pop(guild_id, None)
                                self._track_locks.pop(guild_id, None)
                                self._prev_pressed.discard(guild_id)
                                self._session_stats.pop(guild_id, None)
                                self._playback_retries.pop(guild_id, None)
                                self._pending_playback_retries.pop(guild_id, None)
                                self._live_msgs.pop(guild_id, None)
                                home = getattr(player, "home", None)
                                await player.disconnect()
                                if home:
                                    await home.send(embed=_ok_embed("everyone left — disconnected.", guild.id))
                    finally:
                        self._disconnecting.discard(guild_id)

        if member.id != self.bot.user.id:
            return
        if before.channel is not None and after.channel is None:
            guild_id = member.guild.id
            self._cancel_live_lyrics(guild_id)
            np_view = self._np_views.pop(guild_id, None)
            if np_view:
                await np_view.deactivate()
                if np_view.message:
                    try:
                        await np_view.message.delete()
                    except discord.HTTPException:
                        pass
            self._history.pop(guild_id, None)
            self._track_locks.pop(guild_id, None)
            self._prev_pressed.discard(guild_id)
            self._session_stats.pop(guild_id, None)
            self._playback_retries.pop(guild_id, None)
            self._pending_playback_retries.pop(guild_id, None)
            self._live_msgs.pop(guild_id, None)

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player = payload.player
        track = payload.track
        if not player or not track:
            return

        if hasattr(player, "_skip_votes"):
            player._skip_votes.clear()
            player._skip_initiator = None

        guild_id = player.guild.id
        retries = self._playback_retries.get(guild_id)
        if retries:
            retries.pop(_track_retry_key(track), None)
            if not retries:
                self._playback_retries.pop(guild_id, None)
        self._pending_playback_retries.pop(guild_id, None)

        stats = self._session_stats.setdefault(guild_id, {"tracks": 0, "total_ms": 0, "requesters": {}})
        stats["tracks"] += 1
        stats["total_ms"] += track.length or 0
        rid = getattr(track, "requester_id", None)
        if rid:
            stats["requesters"][rid] = stats["requesters"].get(rid, 0) + 1

        if player.channel:
            await self._update_vc_status(player.channel.id, f"{track.title} | Neixo")

        home = getattr(player, "home", None)
        if home:
            await self._send_now_playing(home, player, track)
            active_view = self._np_views.get(guild_id)
            if active_view:
                asyncio.create_task(self._schedule_lyrics_fetch(player, track, active_view))

    @commands.Cog.listener()
    async def on_wavelink_track_exception(
        self, payload: wavelink.TrackExceptionEventPayload
    ) -> None:
        """A track failed mid-playback (Lavalink exception). Notify and let the queue advance."""
        player = payload.player
        if not player or not player.guild:
            return
        track = payload.track
        title = getattr(track, "title", "unknown")
        error = getattr(payload, "exception", None) or getattr(payload, "error", None)
        log.warning(
            "track exception: %s — %s",
            title,
            error,
        )
        guild_id = player.guild.id
        retry_key = _track_retry_key(track)
        retry_count = self._playback_retries.setdefault(guild_id, {}).get(retry_key, 0)
        if track is not None and retry_count < PLAYBACK_RETRY_LIMIT and _is_retryable_playback_error(error):
            self._playback_retries[guild_id][retry_key] = retry_count + 1
            self._pending_playback_retries[guild_id] = retry_key
            log.warning(
                "retrying transient playback failure for %s (%d/%d)",
                title,
                retry_count + 1,
                PLAYBACK_RETRY_LIMIT,
            )
            return

        self._pending_playback_retries.pop(guild_id, None)
        home = getattr(player, "home", None)
        if home:
            try:
                await home.send(
                    embed=_err_embed(
                        f"playback error on **{title}** — skipping. "
                        f"if YouTube is acting up, try `.playsc {title}` or `.playbc {title}`.",
                        player.guild.id,
                    )
                )
            except discord.HTTPException:
                pass
        # wavelink will fire track_end on its own after an exception; queue
        # advance is handled there so we don't double-skip.

    @commands.Cog.listener()
    async def on_wavelink_track_stuck(
        self, payload: wavelink.TrackStuckEventPayload
    ) -> None:
        """A track stalled (Lavalink reports stuck). Force-skip it."""
        player = payload.player
        if not player or not player.guild:
            return
        track = payload.track
        title = getattr(track, "title", "unknown")
        log.warning("track stuck: %s", title)
        home = getattr(player, "home", None)
        if home:
            try:
                await home.send(
                    embed=_err_embed(f"track **{title}** got stuck — skipping.", player.guild.id)
                )
            except discord.HTTPException:
                pass
        try:
            await player.skip(force=True)
        except Exception as e:
            log.warning("failed to skip stuck track: %s", e)

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player = payload.player
        if not player or not player.guild:
            return

        track = payload.track
        guild_id = player.guild.id
        failed_end = _is_failed_track_end_reason(getattr(payload, "reason", ""))

        # If this end was caused by the ⏮ (prev) button replacing the track,
        # don't run the normal auto-advance / history logic. prev_btn already
        # started the previous track playing and intentionally popped history.
        if guild_id in self._prev_pressed:
            self._prev_pressed.discard(guild_id)
            return

        if guild_id not in self._track_locks:
            self._track_locks[guild_id] = asyncio.Lock()

        next_track: wavelink.Playable | None = None
        np_view: NowPlayingView | None = None
        autoplay_on = False
        home = None
        home_channel = None

        async with self._track_locks[guild_id]:
            queue = player.queue

            if track is not None and self._pending_playback_retries.get(guild_id) == _track_retry_key(track):
                retry_count = self._playback_retries.get(guild_id, {}).get(_track_retry_key(track), 0)
                delay = PLAYBACK_RETRY_BASE_DELAY * retry_count
                if delay > 0:
                    await asyncio.sleep(delay)
                try:
                    await player.play(track)
                    return
                except Exception as e:
                    log.warning("retry playback command failed: %s", e)
                finally:
                    self._pending_playback_retries.pop(guild_id, None)

            if failed_end and track is not None:
                retries = self._playback_retries.get(guild_id)
                if retries:
                    retries.pop(_track_retry_key(track), None)
                    if not retries:
                        self._playback_retries.pop(guild_id, None)

            if not failed_end and queue.mode == wavelink.QueueMode.loop and track is not None:
                # repeat the same track
                next_track = track
            else:
                if track is not None and not failed_end:
                    self._get_history(guild_id).append(track)
                    if queue.mode == wavelink.QueueMode.loop_all:
                        queue.put(track)

                if not queue.is_empty:
                    next_track = queue.get()
                else:
                    np_view = self._np_views.pop(guild_id, None)
                    autoplay_on = player.autoplay == wavelink.AutoPlayMode.enabled
                    home = getattr(player, "home", None)
                    home_channel = player.channel

            if next_track is not None:
                # await inside the lock so a concurrent _queue_tracks can't
                # double-start playback on this guild
                try:
                    await player.play(next_track)
                except Exception as e:
                    log.warning("failed to advance queue: %s", e)
                    # If play failed and queue still has tracks, leave it for
                    # the next event/command to retry. Don't crash.
                return

        # Queue is fully drained — clean up UI and notify outside the lock.
        if np_view:
            await np_view.deactivate()
            if np_view.message:
                try:
                    await np_view.message.delete()
                except discord.HTTPException:
                    pass

        if autoplay_on:
            return

        if home:
            try:
                await home.send(embed=discord.Embed(
                    description=f"-# {Neixoemojis.get('cd')} | queue ended. add more with `.play`.",
                    color=Neixocolor,
                ))
            except discord.HTTPException:
                pass
        if home_channel:
            try:
                await self._update_vc_status(home_channel.id, ".play <song> | Neixo")
            except Exception:
                pass



    # ── commands ──────────────────────────────────────────────

    @commands.command(aliases=["p"])
    @help_meta(
        usage="`.play <query>`",
        desc="Plays a song or playlist. Auto-detects YouTube, Spotify, SoundCloud, and Bandcamp links.",
        section="Playback",
        examples=[
            ".play never gonna give you up",
            ".play https://youtu.be/dQw4w9WgXcQ",
            ".play https://open.spotify.com/track/...",
        ],
        params=[
            {
                "name": "query",
                "type": "str",
                "required": True,
                "desc": "Song name or URL. Supports YouTube, Spotify, SoundCloud, and Bandcamp links.",
            },
        ],
        note="Join a voice channel first. You can queue multiple songs. Spotify playlists are auto-scraped.",
    )
    async def play(self, ctx: commands.Context, *, query: str = None) -> None:
        if not query:
            return await ctx.send(embed=_err_embed("gimme something to play. `.play <query>`", ctx))
        await self._play_core(ctx, query)

    @commands.command(aliases=["sc"])
    @help_meta(
        usage="`.playsc <query>`",
        desc="Plays a song directly from SoundCloud (good fallback when YouTube is blocked).",
        section="Playback",
        examples=[".playsc lofi beats", ".playsc https://soundcloud.com/..."],
        params=[
            {"name": "query", "type": "str", "required": True, "desc": "Song name or SoundCloud URL."},
        ],
        note="Join a voice channel first. Searches SoundCloud directly, bypassing YouTube.",
    )
    async def playsc(self, ctx: commands.Context, *, query: str = None) -> None:
        if not query:
            return await ctx.send(embed=_err_embed("gimme something to play. `.playsc <query>`", ctx))
        await self._play_sc_core(ctx, query)

    @commands.command(aliases=["bc"])
    @help_meta(
        usage="`.playbc <query>`",
        desc="Plays a song directly from Bandcamp without touching YouTube.",
        section="Playback",
        examples=[".playbc undertale soundtrack", ".playbc https://artist.bandcamp.com/track/..."],
        params=[
            {
                "name": "query",
                "type": "str",
                "required": True,
                "desc": "Bandcamp search text or a Bandcamp track/album URL.",
            },
        ],
        note="Join a voice channel first. Direct links are preferred when Bandcamp search is unavailable.",
    )
    async def playbc(self, ctx: commands.Context, *, query: str = None) -> None:
        if not query:
            return await ctx.send(embed=_err_embed("gimme something to play. `.playbc <query>`", ctx))
        await self._play_bandcamp_core(ctx, query)

    @commands.command(aliases=["ytm"])
    @help_meta(
        usage="`.playytm <query>`",
        desc="Plays official studio-quality music tracks from YouTube Music.",
        section="Playback",
        examples=[".playytm starboy", ".ytm blinding lights"],
        params=[
            {"name": "query", "type": "str", "required": True, "desc": "Song name or artist on YouTube Music."},
        ],
        note="Join a voice channel first. Queries YouTube Music directly for high-bitrate studio audio.",
    )
    async def playytm(self, ctx: commands.Context, *, query: str = None) -> None:
        if not query:
            return await ctx.send(embed=_err_embed("gimme something to play. `.playytm <query>`", ctx))
        if not await self._check_vc(ctx):
            return
        player = await self._connect_player(ctx)
        if player is None:
            return
        tracks = await self._yt_search_with_retry(query, source="ytmsearch")
        if not tracks:
            return await ctx.send(embed=_err_embed(f"couldn't find anything on YouTube Music for `{query}`.", ctx))
        if isinstance(tracks, list) and len(tracks) > 1:
            tracks = [self._prefer_audio_track(tracks, query)]
        await self._queue_tracks(ctx, player, tracks, source_label="YouTube Music")

    async def _handle_skip(self, ctx: commands.Context, *, vote_initiator: discord.Member = None) -> None:
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        channel = player.channel
        if not channel:
            return
        listeners = [m for m in channel.members if not m.bot]
        required = max(1, (len(listeners) + 1) // 2)

        if (
            len(listeners) <= 1
            or ctx.author.guild_permissions.manage_guild
            or (player.current and getattr(player.current, "requester_id", None) == ctx.author.id)
        ):
            await player.skip(force=True)
            return await ctx.send(embed=_ok_embed("skipped.", ctx))

        if not hasattr(player, "_skip_votes"):
            player._skip_votes = set()
        if not hasattr(player, "_skip_initiator"):
            player._skip_initiator = None

        if player._skip_initiator is None and vote_initiator is not None:
            player._skip_initiator = vote_initiator

        if ctx.author.id in player._skip_votes:
            return await ctx.send(embed=_err_embed("you already voted to skip this track.", ctx))

        player._skip_votes.add(ctx.author.id)
        votes = len(player._skip_votes)

        if votes >= required:
            player._skip_votes.clear()
            player._skip_initiator = None
            await player.skip(force=True)
            await ctx.send(embed=_ok_embed(f"vote passed ({votes}/{required}) — skipped.", ctx))
        else:
            initiator_name = player._skip_initiator.display_name if player._skip_initiator else "someone"
            await ctx.send(embed=_ok_embed(
                f"skip vote: **{votes}/{required}** — need {required - votes} more vote(s). started by {initiator_name}.",
                ctx
            ))

    @commands.command(aliases=["next"])
    @help_meta(
        usage="`.skip`",
        desc="Skips the current track. Requires a vote if multiple people are in voice.",
        section="Playback",
        examples=[".skip"],
        params=[],
        note="If you're alone, the track skips immediately. In a group, a vote is started.",
    )
    async def skip(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        await self._handle_skip(ctx, vote_initiator=ctx.author)

    @commands.command(aliases=["vs"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    @help_meta(
        usage="`.voteskip`",
        desc="Starts a vote to skip the current track.",
        section="Playback",
        examples=[".voteskip"],
        params=[],
        note="Works in tandem with `.skip`. Majority vote is required in group sessions. 10s cooldown between votes.",
    )
    async def voteskip(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        await self._handle_skip(ctx, vote_initiator=ctx.author)

    @commands.command(aliases=["dc", "stop"])
    @help_meta(
        usage="`.disconnect`",
        desc="Stops playback, clears the queue, and leaves the voice channel.",
        section="Playback",
        examples=[".disconnect"],
        params=[],
        note="Use this instead of just leaving the VC — it ensures proper cleanup.",
    )
    async def disconnect(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        if not player:
            return await ctx.send(embed=_err_embed("not connected.", ctx))

        guild_id = ctx.guild.id
        self._cancel_live_lyrics(guild_id)
        np_view = self._np_views.pop(guild_id, None)
        if np_view:
            await np_view.deactivate()
            if np_view.message:
                try:
                    await np_view.message.delete()
                except discord.HTTPException:
                    pass

        self._history.pop(guild_id, None)
        self._track_locks.pop(guild_id, None)
        self._prev_pressed.discard(guild_id)
        self._live_msgs.pop(guild_id, None)
        self._session_stats.pop(guild_id, None)
        self._playback_retries.pop(guild_id, None)
        self._pending_playback_retries.pop(guild_id, None)
        await player.disconnect()
        await ctx.send(embed=_ok_embed("disconnected.", ctx))

    @commands.command(aliases=["vol"])
    @help_meta(
        usage="`.volume [1-200]`",
        desc="Checks or sets the playback volume (1-200%).",
        section="Playback",
        examples=[".volume", ".volume 50", ".volume 150"],
        params=[
            {"name": "level", "type": "int", "required": False, "desc": "Volume level (1-200). Omit to check current volume."},
        ],
        note="Default is 100%. Values above 100 may cause distortion.",
    )
    async def volume(self, ctx: commands.Context, value: int = None) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        if value is None:
            return await ctx.send(embed=_ok_embed(f"current volume: **{player.volume}**", ctx))
        value = max(1, min(200, value))
        await player.set_volume(value)
        await ctx.send(embed=_ok_embed(f"volume set to **{value}**.", ctx))

    @commands.command()
    @help_meta(
        usage="`.pause`",
        desc="Pauses the current playback.",
        section="Playback",
        examples=[".pause"],
        params=[],
        note="Use `.resume` to continue playing.",
    )
    async def pause(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        await player.pause(True)
        await ctx.send(embed=_ok_embed("paused.", ctx))
        await self._update_vc_status(player.channel.id, "paused | Neixo")

    @commands.command()
    @help_meta(
        usage="`.resume`",
        desc="Resumes paused playback.",
        section="Playback",
        examples=[".resume"],
        params=[],
        note="Only works if playback is currently paused.",
    )
    async def resume(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        if not player.paused:
            return await ctx.send(embed=_err_embed("not paused.", ctx))
        await player.pause(False)
        await ctx.send(embed=_ok_embed("resumed.", ctx))
        await self._update_vc_status(player.channel.id, f"{player.current.title} | Neixo")

    @commands.command(aliases=["np", "now"])
    @help_meta(
        usage="`.nowplaying`",
        desc="Shows the currently playing track with a generated music card.",
        section="Playback",
        examples=[".nowplaying"],
        params=[],
        note="Displays an interactive embed with album art, progress bar, and controls.",
    )
    async def nowplaying(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        track = player.current
        msg = await ctx.send(embed=discord.Embed(
            description=f"**{track.title}** by {track.author}\n-# generating card...",
            color=Neixocolor,
        ))

        pos = player.position
        try:
            progress = pos / track.length if track.length else 0.0
            card_file = await _gen_music_card(
                track.title,
                track.author,
                track.artwork,
                _fmt_time(track.length),
                progress=progress,
                position_str=_fmt_time(pos),
                session=self._http_session
            )
        except Exception as e:
            log.warning("nowplaying card gen failed: %s", e)
            return await msg.edit(embed=_err_embed("couldn't generate card.", ctx))

        if not player.current or player.current != track:
            return await msg.edit(embed=_err_embed("track changed before card could generate.", ctx))

        embed = discord.Embed(
            description=f"-# **[{track.title}]({track.uri})** by {track.author}",
            color=Neixocolor,
        )
        embed.set_image(url="attachment://neixomusiccard.png")
        await msg.edit(embed=embed, attachments=[card_file])

    @commands.command()
    @help_meta(
        usage="`.loop`",
        desc="Opens a dropdown to set the loop mode (track / queue / off).",
        section="Playback",
        examples=[".loop"],
        params=[],
        note="Select from the dropdown: Track (repeat one), Queue (repeat all), or Off.",
    )
    async def loop(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        labels = {
            wavelink.QueueMode.loop: "looping **track**",
            wavelink.QueueMode.loop_all: "looping **queue**",
            wavelink.QueueMode.normal: "loop **disabled**",
        }
        embed = discord.Embed(
            description=f"-# {Neixoemojis.get('check')} | {labels.get(player.queue.mode, 'unknown')}",
            color=Neixocolor,
        )
        view = LoopView(player, ctx.author.id)
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()

    @commands.command()
    @help_meta(
        usage="`.shuffle`",
        desc="Shuffles the current queue into a random order.",
        section="Playback",
        examples=[".shuffle"],
        params=[],
        note="The current track continues playing; only upcoming tracks are shuffled.",
    )
    async def shuffle(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        player.queue.shuffle()
        await ctx.send(embed=_ok_embed("queue shuffled.", ctx))

    @commands.command()
    @help_meta(
        usage="`.autoplay`",
        desc="Toggles autoplay — plays similar tracks after the queue ends.",
        section="Playback",
        examples=[".autoplay"],
        params=[],
        note="When enabled, the bot will automatically add similar tracks when the queue is empty.",
    )
    async def autoplay(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        if player.autoplay == wavelink.AutoPlayMode.enabled:
            player.autoplay = wavelink.AutoPlayMode.disabled
            label = "disabled"
        else:
            player.autoplay = wavelink.AutoPlayMode.enabled
            label = "enabled"
        await ctx.send(embed=_ok_embed(f"autoplay **{label}**.", ctx))

    @commands.command()
    @help_meta(
        usage="`.seek <time>`",
        desc="Jumps to a specific position in the current track.",
        section="Controls",
        examples=[".seek 1:30", ".seek 90"],
        params=[
            {"name": "time", "type": "str", "required": True, "desc": "Target position: `1:30` (mm:ss) or `90` (seconds)."},
        ],
        note="Not supported on all tracks (e.g. live streams).",
    )
    async def seek(self, ctx: commands.Context, *, position: str) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        try:
            parts = position.strip().split(":")
            if len(parts) == 3:
                ms = (int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])) * 1000
            elif len(parts) == 2:
                ms = (int(parts[0]) * 60 + int(parts[1])) * 1000
            else:
                ms = int(parts[0]) * 1000
        except ValueError:
            return await ctx.send(embed=_err_embed("use format `1:30`, `90`, or `1:30:00`.", ctx))
        max_ms = player.current.length if player.current.length is not None else ms
        ms = max(0, min(ms, max_ms))
        await player.seek(ms)
        await ctx.send(embed=_ok_embed(f"seeked to `{_fmt_time(ms)}`.", ctx))
        # Restart live lyrics if active
        guild_id = ctx.guild.id
        if guild_id in self._live_tasks:
            view = self._np_views.get(guild_id)
            synced = view._synced_lines if view else None
            if synced and player.current:
                await self._start_live_lyrics(ctx.channel, player, synced, player.current)
    @commands.command(aliases=["ff"])
    @help_meta(
        usage="`.fastforward [seconds]`",
        desc="Fast-forwards by a number of seconds (default 10s).",
        section="Controls",
        examples=[".fastforward", ".fastforward 30"],
        params=[
            {"name": "seconds", "type": "int", "required": False, "desc": "Seconds to skip forward (default 10)."},
        ],
        note="Not supported on live streams.",
    )
    async def fastforward(self, ctx: commands.Context, seconds: int = 10) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        max_ms = player.current.length if player.current.length is not None else player.position + seconds * 1000
        new_pos = min(player.position + seconds * 1000, max_ms)
        await player.seek(new_pos)
        await ctx.send(embed=_ok_embed(f"fast forwarded to `{_fmt_time(new_pos)}`.", ctx))
        guild_id = ctx.guild.id
        if guild_id in self._live_tasks:
            view = self._np_views.get(guild_id)
            synced = view._synced_lines if view else None
            if synced and player.current:
                await self._start_live_lyrics(ctx.channel, player, synced, player.current)

    @commands.command(aliases=["rw"])
    @help_meta(
        usage="`.rewind [seconds]`",
        desc="Rewinds by a number of seconds (default 10s).",
        section="Controls",
        examples=[".rewind", ".rewind 30"],
        params=[
            {"name": "seconds", "type": "int", "required": False, "desc": "Seconds to go back (default 10)."},
        ],
        note="Not supported on live streams.",
    )
    async def rewind(self, ctx: commands.Context, seconds: int = 10) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        new_pos = max(0, player.position - seconds * 1000)
        await player.seek(new_pos)
        await ctx.send(embed=_ok_embed(f"rewound to `{_fmt_time(new_pos)}`.", ctx))
        guild_id = ctx.guild.id
        if guild_id in self._live_tasks:
            view = self._np_views.get(guild_id)
            synced = view._synced_lines if view else None
            if synced and player.current:
                await self._start_live_lyrics(ctx.channel, player, synced, player.current)

    @commands.command()
    @help_meta(
        usage="`.replay`",
        desc="Restarts the current track from the beginning.",
        section="Controls",
        examples=[".replay"],
        params=[],
        note="Works like a seek to position 0.",
    )
    async def replay(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        await player.seek(0)
        await ctx.send(embed=_ok_embed("restarted current track.", ctx))
        guild_id = ctx.guild.id
        if guild_id in self._live_tasks:
            view = self._np_views.get(guild_id)
            synced = view._synced_lines if view else None
            if synced and player.current:
                await self._start_live_lyrics(ctx.channel, player, synced, player.current)

    @commands.command(aliases=["sim"])
    @help_meta(
        usage="`.similar`",
        desc="Shows similar tracks to the current one. Pick up to 7 to add to the queue.",
        section="Playback",
        examples=[".similar"],
        params=[],
        note="Uses the current track to recommend similar songs via a selection menu.",
    )
    async def similar(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        vid_id = None
        if hasattr(player.current, "identifier"):
            vid_id = player.current.identifier
        elif hasattr(player.current, "uri"):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(player.current.uri).query)
            vid_id = params.get("v", [None])[0]
        if not vid_id:
            return await ctx.send(embed=_err_embed("similar only works on YouTube tracks. current track has no YouTube ID.", ctx))
        tracks = await self._fetch_similar(vid_id, cap=10)
        if not tracks:
            return await ctx.send(embed=_err_embed("no similar tracks found.", ctx))
        desc = f"{Neixoemojis.get('cd')} | pick tracks to add to queue.\n\n"
        for i, t in enumerate(tracks, 1):
            desc += f"-# {i}. [{t['title']}]({t['url']})\n"
        embed = discord.Embed(description=desc, color=Neixocolor)
        view = SimilarView(tracks, player, ctx.author.id)
        await ctx.send(embed=embed, view=view)

    @commands.command(aliases=["dh", "genre", "hits"])
    @help_meta(
        usage="`.dailyhits`",
        desc="Opens a genre dropdown to load daily hit tracks.",
        section="Playback",
        examples=[".dailyhits"],
        params=[],
        note="Pick a genre from the dropdown to see today's top tracks in that genre.",
    )
    async def dailyhits(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx):
            return
        if not ctx.voice_client:
            if ctx.author.voice and ctx.author.voice.channel:
                player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
                player.home = ctx.channel
                player.autoplay = wavelink.AutoPlayMode.partial
        view = GenreView(self, ctx)
        embed = discord.Embed(
            description=f"-# {Neixoemojis.get('rightarrow')} | pick a genre to load tracks.",
            color=Neixocolor,
        )
        await ctx.send(embed=embed, view=view)

    # ── lyrics ────────────────────────────────────────────────

    @commands.command(aliases=["l", "lyric"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    @help_meta(
        usage="`.lyrics [query]`",
        desc="Fetches lyrics for the current track or a search query. Results are paginated.",
        section="Playback",
        examples=[".lyrics", ".lyrics never gonna give you up"],
        params=[
            {"name": "query", "type": "str", "required": False, "desc": "Song name to search lyrics for. Omit to use current track."},
        ],
        note="Uses syncedlyrics and LRCLIB. Supports synced/karaoke-style lyrics. 10s cooldown between uses.",
    )
    async def lyrics(self, ctx: commands.Context, *, query: str = None) -> None:
        if query:
            clean = self._clean_query(query)
            if " - " in clean:
                artist, title = clean.split(" - ", 1)
            else:
                artist, title = "", clean
            result = await self._fetch_lyrics(artist, title)
            if not result:
                genius_link = f"https://genius.com/search?q={urllib.parse.quote(clean)}"
                embed = discord.Embed(
                    description=f"Couldn't find lyrics for **{query}**.\n[Search on Genius]({genius_link})",
                    color=Neixocolor,
                )
                return await ctx.send(embed=embed)
            lyrics = result[0]
        else:
            if not await self._check_vc(ctx) or not await self._check_playing(ctx):
                return
            player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
            track = player.current
            artist = track.author
            title = track.title
            result = await self._fetch_lyrics(artist, title)
            if not result:
                genius_link = f"https://genius.com/search?q={urllib.parse.quote(f'{artist} {title}')}"
                embed = discord.Embed(
                    description=f"Couldn't find lyrics for **{title}** by **{artist}**.\n[Search on Genius]({genius_link})",
                    color=Neixocolor,
                )
                return await ctx.send(embed=embed)
            lyrics = result[0]

        await self._send_lyrics(ctx, lyrics, artist, title)

    @lyrics.error
    async def lyrics_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(embed=_err_embed("Please wait 10 seconds before using this command again.", ctx), delete_after=5)

    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.user)
    @help_meta(
        usage="`.sync`",
        desc="Forces synced karaoke-style lyrics for the current track.",
        section="Playback",
        examples=[".sync"],
        params=[],
        note="Only works if synced lyrics are available for the current track. 10s cooldown between uses.",
    )
    async def sync(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        track = player.current
        guild_id = player.guild.id

        # Check if view already has synced lines
        view = self._np_views.get(guild_id)
        synced = view._synced_lines if view else None

        if not synced:
            # Force fetch immediately (no 5s delay)
            result = await self._fetch_lyrics(track.author, track.title)
            if result:
                _, synced = result
                # Also populate the view if it exists
                if view and synced:
                    view._synced_lines = synced
                    view._lyrics_data = (track.author, track.title, result[0])
                    view.lyrics_btn.disabled = False
                    view.lyrics_btn.emoji = "<a:emoji_44:1253070278259642521>"
                    try:
                        await view.message.edit(view=view)
                    except discord.HTTPException:
                        pass

        if not synced:
            return await ctx.send(embed=_err_embed("no synced lyrics available for this track.", ctx))

        # If already running, seek back 5s to resync
        if guild_id in self._live_tasks and not self._live_tasks[guild_id].done():
            seek_to = max(0, int(player.position) - 5000)
            await player.seek(seek_to)
        await self._start_live_lyrics(ctx.channel, player, synced, track)

    async def _send_lyrics(self, ctx: commands.Context, lyrics: str, artist: str, title: str):
        lines = lyrics.split("\n")
        per_page = 25
        pages = [lines[i:i+per_page] for i in range(0, len(lines), per_page)]
        view = LyricsPaginationView(ctx.author.id, pages, title=title, artist=artist)
        embed = view.build_embed()
        view.message = await ctx.send(embed=embed, view=view)

    async def _send_lyrics_to_channel(
        self,
        channel: discord.TextChannel,
        lyrics: str,
        artist: str,
        title: str,
        author_id: int,
    ) -> discord.Message:
        lines = lyrics.split("\n")
        per_page = 25
        pages = [lines[i:i+per_page] for i in range(0, len(lines), per_page)]
        view = LyricsPaginationView(author_id, pages, title=title, artist=artist)
        embed = view.build_embed()
        msg = await channel.send(embed=embed, view=view)
        view.message = msg
        return msg

    # ── queue group ───────────────────────────────────────────

    @commands.group(name="queue", aliases=["q"], invoke_without_command=True)
    @help_meta(
        usage="`.queue`",
        desc="Shows the current queue with total duration and loop mode. Paginated.",
        section="Controls",
        examples=[".queue"],
        params=[],
        note="Use `.queue remove`, `.queue shuffle`, `.queue empty`, `.queue move`, `.queue skipto`, or `.queue dedupe` for more queue management.",
    )
    async def queue_group(self, ctx: commands.Context) -> None:
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        if not player or not player.current:
            return await ctx.send(embed=_err_embed("nothing playing.", ctx))
        if player.autoplay == wavelink.AutoPlayMode.enabled and player.queue.is_empty:
            embed = discord.Embed(
                description=f"-# {Neixoemojis.get('cd')} | autoplay is on — wavelink is picking the next track automatically.",
                color=Neixocolor,
            )
            return await ctx.send(embed=embed)
        view = QueueView(ctx, player)
        view.message = await ctx.send(embed=view.build_embed(), view=view)

    @queue_group.command(name="remove", aliases=["rm"])
    @help_meta(
        usage="`.queue remove <position>`",
        desc="Removes a track from the queue by its position number.",
        section="Controls",
        examples=[".queue remove 3"],
        params=[
            {"name": "position", "type": "int", "required": True, "desc": "Position of the track in the queue to remove."},
        ],
        note="Use `.queue` to see positions.",
    )
    async def queue_remove(self, ctx: commands.Context, index: int) -> None:
        if not await self._check_vc(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        q = list(player.queue)
        if not q:
            return await ctx.send(embed=_err_embed("queue is empty.", ctx))
        if index < 1 or index > len(q):
            return await ctx.send(embed=_err_embed(f"invalid number. pick 1–{len(q)}.", ctx))
        removed = q[index - 1]
        player.queue.remove(removed)
        await ctx.send(embed=_ok_embed(f"removed **{removed.title}**.", ctx))

    @queue_group.command(name="shuffle")
    @help_meta(
        usage="`.queue shuffle`",
        desc="Shuffles the queue in place.",
        section="Controls",
        examples=[".queue shuffle"],
        params=[],
        note="Same as `.shuffle` but as a queue subcommand.",
    )
    async def queue_shuffle(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        player.queue.shuffle()
        await ctx.send(embed=_ok_embed("queue shuffled.", ctx))

    @queue_group.command(name="empty", aliases=["clear"])
    @help_meta(
        usage="`.queue empty`",
        desc="Clears the entire queue.",
        section="Controls",
        examples=[".queue empty"],
        params=[],
        note="The current track will continue playing.",
    )
    async def queue_empty(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        player.queue.clear()
        await ctx.send(embed=_ok_embed("queue cleared.", ctx))

    @queue_group.command(name="move", aliases=["mv"])
    @help_meta(
        usage="`.queue move <from> <to>`",
        desc="Moves a track from one position to another in the queue.",
        section="Controls",
        examples=[".queue move 5 2"],
        params=[
            {"name": "from", "type": "int", "required": True, "desc": "Current position of the track."},
            {"name": "to", "type": "int", "required": True, "desc": "New position for the track."},
        ],
        note="Use `.queue` to see positions.",
    )
    async def queue_move(self, ctx: commands.Context, position: int, new_position: int) -> None:
        if not await self._check_vc(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        q = list(player.queue)
        if not q:
            return await ctx.send(embed=_err_embed("queue is empty.", ctx))
        length = len(q)
        if not (1 <= position <= length) or not (1 <= new_position <= length):
            return await ctx.send(embed=_err_embed(f"positions must be between 1 and {length}.", ctx))
        if position == new_position:
            return await ctx.send(embed=_err_embed("that's already in that position.", ctx))
        track = q.pop(position - 1)
        q.insert(new_position - 1, track)
        player.queue.clear()
        for t in q:
            player.queue.put(t)
        await ctx.send(embed=_ok_embed(f"moved **{track.title}** to position **{new_position}**.", ctx))

    @queue_group.command(name="skipto", aliases=["st"])
    @help_meta(
        usage="`.queue skipto <pos>`",
        desc="Skips directly to a specific position in the queue.",
        section="Controls",
        examples=[".queue skipto 5"],
        params=[
            {"name": "pos", "type": "int", "required": True, "desc": "Queue position to skip to."},
        ],
        note="All tracks before the target position are removed.",
    )
    async def queue_skipto(self, ctx: commands.Context, position: int) -> None:
        if not await self._check_vc(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        q = list(player.queue)
        if not q:
            return await ctx.send(embed=_err_embed("queue is empty.", ctx))
        if position < 1 or position > len(q):
            return await ctx.send(embed=_err_embed(f"invalid position. pick 1–{len(q)}.", ctx))
        target = q[position - 1]
        player.queue.clear()
        await player.queue.put_wait(target)
        for t in q[position:]:
            await player.queue.put_wait(t)
        await player.skip(force=True)
        await ctx.send(embed=_ok_embed(f"skipped to **{target.title}** (position {position}).", ctx))

    @queue_group.command(name="dedupe")
    @help_meta(
        usage="`.queue dedupe`",
        desc="Removes duplicate tracks from the queue.",
        section="Controls",
        examples=[".queue dedupe"],
        params=[],
        note="Keeps only the first occurrence of each track.",
    )
    async def queue_dedupe(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        q = list(player.queue)
        if not q:
            return await ctx.send(embed=_err_embed("queue is empty.", ctx))
        seen = set()
        unique = []
        removed = 0
        for t in q:
            uri = getattr(t, "uri", None) or getattr(t, "identifier", str(id(t)))
            if uri in seen:
                removed += 1
            else:
                seen.add(uri)
                unique.append(t)
        if removed == 0:
            return await ctx.send(embed=_ok_embed("no duplicates found.", ctx))
        player.queue.clear()
        for t in unique:
            await player.queue.put_wait(t)
        await ctx.send(embed=_ok_embed(f"removed **{removed}** duplicate(s).", ctx))

    # ── preset filter dropdown ────────────────────────────────

    @commands.command()
    @help_meta(
        usage="`.preset`",
        desc="Opens a dropdown to browse and apply audio filters.",
        section="Filters",
        examples=[".preset"],
        params=[],
        note="Select from available filter presets. Use `.clearfilter` to reset.",
    )
    async def preset(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)

        options = [
            discord.SelectOption(label="Bass Boost", value="bassboost"),
            discord.SelectOption(label="Nightcore", value="nightcore"),
            discord.SelectOption(label="Lofi", value="lofi"),
            discord.SelectOption(label="Slowed+Reverb", value="slowed"),
            discord.SelectOption(label="8D Audio", value="eightd"),
            discord.SelectOption(label="Concert", value="concert"),
            discord.SelectOption(label="Dolby", value="dolby"),
            discord.SelectOption(label="Heaven", value="heaven"),
            discord.SelectOption(label="Instrumental", value="instrumental"),
            discord.SelectOption(label="Muffled", value="muffled"),
            discord.SelectOption(label="Dreamcore", value="dreamcore"),
            discord.SelectOption(label="Tremolo", value="tremolocmd"),
            discord.SelectOption(label="Vibrato", value="vibrato"),
            discord.SelectOption(label="Rotation", value="rotation"),
            discord.SelectOption(label="Reverse Room", value="reverseroom"),
            discord.SelectOption(label="Clear Filters", value="clearfilter"),
        ]

        select = Select(placeholder="Pick a filter", options=options, min_values=1, max_values=1)

        async def select_callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != ctx.author.id:
                return await interaction.response.send_message("Not your command.", ephemeral=True)
            chosen = select.values[0]
            cmd = self.bot.get_command(chosen)
            if cmd:
                await interaction.response.defer()
                await ctx.invoke(cmd)
            else:
                await interaction.response.send_message("unknown filter.", ephemeral=True)

        select.callback = select_callback
        view = View(timeout=60)
        view.add_item(select)
        await ctx.send(embed=discord.Embed(description="-# pick a filter below.", color=Neixocolor), view=view)

    # ── individual filter commands ────────────────────────────

    @commands.command(aliases=["bass", "bb"])
    @help_meta(
        usage="`.bassboost`",
        desc="Applies a heavy bass boost filter — punchy low-end.",
        section="Filters",
        examples=[".bassboost"],
        params=[],
        note="Toggle effect. Use `.clearfilter` to remove.",
    )
    async def bassboost(self, ctx: commands.Context) -> None:
        # Standard 15-band Lavalink EQ: bands 0–4 cover 25 Hz to 160 Hz.
        # Boost the low end smoothly without the hidden detune the old preset had.
        f = wavelink.Filters()
        f.equalizer.set(bands=[
            {"band": 0, "gain": 0.55},   # 25 Hz
            {"band": 1, "gain": 0.45},   # 40 Hz
            {"band": 2, "gain": 0.35},   # 63 Hz
            {"band": 3, "gain": 0.20},   # 100 Hz
            {"band": 4, "gain": 0.05},   # 160 Hz
        ])
        await self._apply_filter(ctx, f, "bass boost")

    @commands.command()
    @help_meta(
        usage="`.nightcore`",
        desc="Applies a higher pitch and speed filter — anime/nightcore style.",
        section="Filters",
        examples=[".nightcore"],
        params=[],
        note="Toggle effect. Use `.clearfilter` to remove.",
    )
    async def nightcore(self, ctx: commands.Context) -> None:
        # Classic anime nightcore: pitch + speed up by 20%.
        # Cut a hair off the lowest sub-bass (gets boomy at high speed) and
        # tame the very top so it isn't ear-piercing.
        f = wavelink.Filters()
        f.timescale.set(speed=1.2, pitch=1.2, rate=1.0)
        f.equalizer.set(bands=[
            {"band": 0, "gain": -0.05},
            {"band": 1, "gain": -0.05},
            {"band": 13, "gain": -0.05},
            {"band": 14, "gain": -0.10},
        ])
        await self._apply_filter(ctx, f, "nightcore")

    @commands.command()
    @help_meta(
        usage="`.lofi`",
        desc="Applies a low-fidelity filter — mellow and warm sound.",
        section="Filters",
        examples=[".lofi"],
        params=[],
        note="Toggle effect. Use `.clearfilter` to remove.",
    )
    async def lofi(self, ctx: commands.Context) -> None:
        # Mellow, warm, slightly slowed — classic study-beats vibe.
        # Roll off everything above 1.6 kHz progressively for that vinyl-warm
        # feel, slight bass lift, and a low-pass on top for the muffled tape sound.
        f = wavelink.Filters()
        f.timescale.set(speed=0.85, pitch=0.85, rate=1.0)
        f.low_pass.set(smoothing=20.0)
        f.equalizer.set(bands=[
            {"band": 0, "gain": 0.15},
            {"band": 1, "gain": 0.15},
            {"band": 2, "gain": 0.10},
            {"band": 9,  "gain": -0.10},
            {"band": 10, "gain": -0.15},
            {"band": 11, "gain": -0.20},
            {"band": 12, "gain": -0.25},
            {"band": 13, "gain": -0.30},
            {"band": 14, "gain": -0.30},
        ])
        await self._apply_filter(ctx, f, "lofi")

    @commands.command()
    @help_meta(
        usage="`.slowed`",
        desc="Applies a slowed + reverb effect — chill and dreamy.",
        section="Filters",
        examples=[".slowed"],
        params=[],
        note="Toggle effect. Use `.clearfilter` to remove.",
    )
    async def slowed(self, ctx: commands.Context) -> None:
        # Slowed-and-reverb style. Lavalink has no real reverb, so we fake the
        # "spacey" feel with a soft low-pass + a gentle low-frequency tremolo,
        # plus a slowed pitched-down timescale.
        # Crucially: no karaoke filter — the old preset was deleting the vocals.
        f = wavelink.Filters()
        f.timescale.set(speed=0.83, pitch=0.92, rate=1.0)
        f.low_pass.set(smoothing=15.0)
        f.tremolo.set(frequency=0.6, depth=0.10)
        f.equalizer.set(bands=[
            {"band": 0, "gain": 0.10},
            {"band": 1, "gain": 0.10},
            {"band": 13, "gain": -0.10},
            {"band": 14, "gain": -0.15},
        ])
        await self._apply_filter(ctx, f, "slowed + reverb")

    @commands.command()
    @help_meta(
        usage="`.concert`",
        desc="Applies a live concert hall reverb effect.",
        section="Filters",
        examples=[".concert"],
        params=[],
        note="Toggle effect. Use `.clearfilter` to remove.",
    )
    async def concert(self, ctx: commands.Context) -> None:
        # Live-hall feel: classic "smile" EQ (boosted bass + treble, slight mid scoop)
        # plus a small amount of cross-channel bleed for stereo widening.
        # The old preset's channel_mix used identity values (1/0/0/1) which was
        # a no-op — fixed below.
        f = wavelink.Filters()
        f.equalizer.set(bands=[
            {"band": 0, "gain": 0.20},
            {"band": 1, "gain": 0.20},
            {"band": 2, "gain": 0.15},
            {"band": 3, "gain": 0.10},
            {"band": 6, "gain": -0.05},
            {"band": 7, "gain": -0.05},
            {"band": 12, "gain": 0.10},
            {"band": 13, "gain": 0.15},
            {"band": 14, "gain": 0.20},
        ])
        f.channel_mix.set(
            left_to_left=0.95,  left_to_right=0.10,
            right_to_left=0.10, right_to_right=0.95,
        )
        await self._apply_filter(ctx, f, "concert")

    @commands.command()
    @help_meta(
        usage="`.eightd`",
        desc="Applies 8D audio — sound rotates around your head.",
        section="Filters",
        examples=[".eightd"],
        params=[],
        note="Toggle effect. Best experienced with headphones. Use `.clearfilter` to remove.",
    )
    async def eightd(self, ctx: commands.Context) -> None:
        # 8D — rotation_hz=0.2 is the standard "around your head" speed.
        # The old 0.28 was too fast and made it feel dizzy.
        # This is the ONLY preset that should use rotation.
        f = wavelink.Filters()
        f.rotation.set(rotation_hz=0.2)
        f.equalizer.set(bands=[
            {"band": 0, "gain": 0.15},
            {"band": 1, "gain": 0.10},
            {"band": 13, "gain": 0.05},
        ])
        await self._apply_filter(ctx, f, "8D")

    @commands.command()
    @help_meta(
        usage="`.dolby`",
        desc="Applies a Dolby surround sound effect.",
        section="Filters",
        examples=[".dolby"],
        params=[],
        note="Toggle effect. Use `.clearfilter` to remove.",
    )
    async def dolby(self, ctx: commands.Context) -> None:
        # Surround / cinematic feel: smile EQ for big bass + sparkly highs,
        # plus a meaningful amount of channel cross-bleed for spatial spread.
        # Removed the rotation filter (that was making it spin like 8D) and
        # the karaoke filter (was eating the vocals).
        f = wavelink.Filters()
        f.equalizer.set(bands=[
            {"band": 0, "gain": 0.30},
            {"band": 1, "gain": 0.25},
            {"band": 2, "gain": 0.15},
            {"band": 5, "gain": -0.05},
            {"band": 6, "gain": -0.05},
            {"band": 12, "gain": 0.15},
            {"band": 13, "gain": 0.20},
            {"band": 14, "gain": 0.25},
        ])
        f.channel_mix.set(
            left_to_left=0.85,  left_to_right=0.20,
            right_to_left=0.20, right_to_right=0.85,
        )
        await self._apply_filter(ctx, f, "dolby surround", vol_cap=90)

    @commands.command()
    @help_meta(
        usage="`.heaven`",
        desc="Applies an ethereal, airy filter — floaty and soft.",
        section="Filters",
        examples=[".heaven"],
        params=[],
        note="Toggle effect. Use `.clearfilter` to remove.",
    )
    async def heaven(self, ctx: commands.Context) -> None:
        # Floaty, airy, slightly slowed and lifted in pitch like a music box.
        # Highs sparkle, lows are gentle, slight stereo widening for "open" feel.
        # Removed the rotation (was spinning) and karaoke (was killing vocals).
        f = wavelink.Filters()
        f.timescale.set(speed=0.92, pitch=1.05, rate=1.0)
        f.equalizer.set(bands=[
            {"band": 0, "gain": 0.05},
            {"band": 1, "gain": 0.05},
            {"band": 6, "gain": -0.05},
            {"band": 7, "gain": -0.05},
            {"band": 12, "gain": 0.20},
            {"band": 13, "gain": 0.30},
            {"band": 14, "gain": 0.35},
        ])
        f.channel_mix.set(
            left_to_left=0.90,  left_to_right=0.15,
            right_to_left=0.15, right_to_right=0.90,
        )
        await self._apply_filter(ctx, f, "heaven", vol_cap=85)

    @commands.command(aliases=["karaoke", "vocalscut"])
    @help_meta(
        usage="`.instrumental`",
        desc="Attempts to remove vocals — karaoke mode.",
        section="Filters",
        examples=[".instrumental"],
        params=[],
        note="Toggle effect. Works best on tracks with centered vocals. Use `.clearfilter` to remove.",
    )
    async def instrumental(self, ctx: commands.Context) -> None:
        f = wavelink.Filters()
        f.karaoke.set(level=1.0, mono_level=1.0, filter_band=220.0, filter_width=200.0)
        await self._apply_filter(ctx, f, "instrumental/karaoke", vol_cap=90)

    @commands.command(aliases=["underwater", "muffle"])
    @help_meta(
        usage="`.muffled`",
        desc="Applies a muffled underwater effect.",
        section="Filters",
        examples=[".muffled"],
        params=[],
        note="Toggle effect. Use `.clearfilter` to remove.",
    )
    async def muffled(self, ctx: commands.Context) -> None:
        # Heavy low-pass + progressive treble roll-off for that
        # behind-a-wall / underwater sound. No karaoke (that was unrelated).
        f = wavelink.Filters()
        f.low_pass.set(smoothing=40.0)
        f.equalizer.set(bands=[
            {"band": 0, "gain": 0.10},
            {"band": 1, "gain": 0.10},
            {"band": 8,  "gain": -0.15},
            {"band": 9,  "gain": -0.25},
            {"band": 10, "gain": -0.35},
            {"band": 11, "gain": -0.45},
            {"band": 12, "gain": -0.50},
            {"band": 13, "gain": -0.50},
            {"band": 14, "gain": -0.50},
        ])
        await self._apply_filter(ctx, f, "muffled", vol_cap=90)

    @commands.command(aliases=["tremolo", "trem"])
    @help_meta(
        usage="`.tremolocmd`",
        desc="Applies a tremolo effect — rapid volume oscillation.",
        section="Filters",
        examples=[".tremolocmd"],
        params=[],
        note="Toggle effect. Use `.clearfilter` to remove.",
    )
    async def tremolocmd(self, ctx: commands.Context) -> None:
        # Just amplitude oscillation. The old preset stacked an aggressive
        # bass boost + low-pass on top which fought the effect.
        f = wavelink.Filters()
        f.tremolo.set(frequency=4.0, depth=0.6)
        await self._apply_filter(ctx, f, "tremolo", vol_cap=95)

    @commands.command()
    @help_meta(
        usage="`.vibrato`",
        desc="Applies a vibrato effect — rapid pitch oscillation.",
        section="Filters",
        examples=[".vibrato"],
        params=[],
        note="Toggle effect. Use `.clearfilter` to remove.",
    )
    async def vibrato(self, ctx: commands.Context) -> None:
        # Pitch oscillation. Removed the karaoke filter the old preset had —
        # vibrato has nothing to do with vocal removal.
        f = wavelink.Filters()
        f.vibrato.set(frequency=8.0, depth=0.7)
        await self._apply_filter(ctx, f, "vibrato", vol_cap=95)

    @commands.command(aliases=["dreamy", "dream", "trance"])
    @help_meta(
        usage="`.dreamcore`",
        desc="Applies a trance-like dreamy filter.",
        section="Filters",
        examples=[".dreamcore"],
        params=[],
        note="Toggle effect. Use `.clearfilter` to remove.",
    )
    async def dreamcore(self, ctx: commands.Context) -> None:
        # Dreamy, slightly sped-up trance feel. Subtle tremolo gives the
        # pulsing energy without it feeling jittery.
        # Dropped the rotation filter (was making it spin like 8D) and
        # the over-the-top pitch=1.2 (sounded chipmunk).
        f = wavelink.Filters()
        f.timescale.set(speed=1.05, pitch=1.08, rate=1.0)
        f.tremolo.set(frequency=3.0, depth=0.25)
        f.equalizer.set(bands=[
            {"band": 0, "gain": 0.10},
            {"band": 1, "gain": 0.10},
            {"band": 12, "gain": 0.15},
            {"band": 13, "gain": 0.20},
            {"band": 14, "gain": 0.20},
        ])
        await self._apply_filter(ctx, f, "dreamcore", vol_cap=92)

    @commands.command(aliases=["hall", "cave"])
    @help_meta(
        usage="`.rotation`",
        desc="Applies a hall/spacious cave-like ambience effect.",
        section="Filters",
        examples=[".rotation"],
        params=[],
        note="Toggle effect. For 8D rotation specifically, use `.eightd`. Use `.clearfilter` to remove.",
    )
    async def rotation(self, ctx: commands.Context) -> None:
        # Hall / cave ambience. Lavalink has no real reverb, so we approximate:
        # slight low-pass for "distance", a mid scoop (rooms naturally absorb
        # mids), gentle tremolo for spaciousness, and a small amount of
        # cross-channel bleed so it feels wide instead of dry.
        # Removed the actual rotation filter — that was a left↔right pan effect,
        # not hall ambience. (For 8D rotation, use .eightd.)
        f = wavelink.Filters()
        f.timescale.set(speed=0.97, pitch=0.98, rate=1.0)
        f.low_pass.set(smoothing=8.0)
        f.tremolo.set(frequency=0.7, depth=0.12)
        f.equalizer.set(bands=[
            {"band": 0, "gain": 0.15},
            {"band": 1, "gain": 0.10},
            {"band": 5, "gain": -0.10},
            {"band": 6, "gain": -0.15},
            {"band": 7, "gain": -0.10},
            {"band": 13, "gain": 0.05},
            {"band": 14, "gain": 0.05},
        ])
        f.channel_mix.set(
            left_to_left=0.85,  left_to_right=0.20,
            right_to_left=0.20, right_to_right=0.85,
        )
        await self._apply_filter(ctx, f, "hall", vol_cap=90)

    @commands.command(aliases=["reversefx", "fliproom"])
    @help_meta(
        usage="`.reverseroom`",
        desc="Applies a reverse room — flipped spatial effect.",
        section="Filters",
        examples=[".reverseroom"],
        params=[],
        note="Toggle effect. Use `.clearfilter` to remove.",
    )
    async def reverseroom(self, ctx: commands.Context) -> None:
        # Disorienting flipped-room feel: slow wobble in pitch, slightly
        # slowed playback, and a mid scoop for that "wrong way round" vibe.
        # Dropped the rotation filter (left↔right pan was the wrong tool).
        f = wavelink.Filters()
        f.timescale.set(speed=0.92, pitch=0.95, rate=1.0)
        f.vibrato.set(frequency=2.0, depth=0.4)
        f.low_pass.set(smoothing=6.0)
        f.equalizer.set(bands=[
            {"band": 0, "gain": -0.05},
            {"band": 4, "gain": 0.10},
            {"band": 5, "gain": 0.10},
            {"band": 7, "gain": -0.10},
            {"band": 8, "gain": -0.15},
            {"band": 13, "gain": -0.10},
        ])
        await self._apply_filter(ctx, f, "reverse room", vol_cap=88)

    @commands.command(aliases=["cf", "clearfilters"])
    @help_meta(
        usage="`.clearfilter`",
        desc="Clears all active audio filters and restores clean audio.",
        section="Filters",
        examples=[".clearfilter"],
        params=[],
        note="Turns off all filter effects at once.",
    )
    async def clearfilter(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        await player.set_filters(wavelink.Filters())
        await ctx.send(embed=_ok_embed("all filters cleared.", ctx))

    @commands.command()
    @help_meta(
        usage="`.save`",
        desc="DMs you the current track info so you don't lose it.",
        section="Playback",
        examples=[".save"],
        params=[],
        note="Sends a DM with the track name, artist, and URL.",
    )
    async def save(self, ctx: commands.Context) -> None:
        if not await self._check_vc(ctx) or not await self._check_playing(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        track = player.current
        embed = discord.Embed(
            description=f"**[{track.title}]({track.uri})**\nby {track.author} — {_fmt_time(track.length)}",
            color=Neixocolor,
        )
        if track.artwork:
            embed.set_thumbnail(url=track.artwork)
        try:
            await ctx.author.send(embed=embed)
            await ctx.send(embed=_ok_embed("sent to your DMs.", ctx))
        except discord.Forbidden:
            await ctx.send(embed=_err_embed("couldn't DM you. check your privacy settings.", ctx))

    @commands.command(aliases=["recent"])
    @help_meta(
        usage="`.history`",
        desc="Shows the last 10 tracks that were played in this session.",
        section="Playback",
        examples=[".history"],
        params=[],
        note="Session history resets when the bot leaves the voice channel.",
    )
    async def history(self, ctx: commands.Context) -> None:
        hist = self._history.get(ctx.guild.id)
        if not hist:
            return await ctx.send(embed=_err_embed("no history yet.", ctx))
        tracks = list(reversed(hist))[:10]
        desc = "\n".join(f"-# {i}. [{t.title}]({t.uri})" for i, t in enumerate(tracks, 1))
        await ctx.send(embed=discord.Embed(
            description=f"{Neixoemojis.get('cd')} | **recently played**\n\n{desc}",
            color=Neixocolor,
        ))

    @commands.command(aliases=["pn"])
    @help_meta(
        usage="`.playnext <query>`",
        desc="Adds a track to the front of the queue so it plays next.",
        section="Playback",
        examples=[".playnext never gonna give you up", ".playnext https://youtu.be/dQw4w9WgXcQ"],
        params=[
            {"name": "query", "type": "str", "required": True, "desc": "Song name or URL. Same sources as `.play`."},
        ],
        note="The track is inserted at position 1 in the queue (after the current track).",
    )
    async def playnext(self, ctx: commands.Context, *, query: str = None) -> None:
        if not query:
            return await ctx.send(embed=_err_embed("gimme a track. `.playnext <query>`", ctx))
        if not await self._check_vc(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        if not player:
            return await ctx.send(embed=_err_embed("not connected.", ctx))
        tracks = await self._search_with_fallback(query)
        if not tracks:
            return await ctx.send(embed=_err_embed("nothing found.", ctx))
        if isinstance(tracks, list) and len(tracks) > 1:
            tracks = [self._prefer_audio_track(tracks, query)]
        track = tracks[0] if isinstance(tracks, list) else tracks
        if isinstance(track, wavelink.Playlist):
            track = next(
                (t for t in track.tracks if _is_track_allowed(t)[0]),
                None,
            )
            if track is None:
                return await ctx.send(
                    embed=_err_embed("no playable track in that result (too long / live).", ctx)
                )
        ok, reason = _is_track_allowed(track)
        if not ok:
            return await ctx.send(embed=_err_embed(reason, ctx))
        track.requester_id = ctx.author.id
        player.queue.put_at(0, track)
        await ctx.send(embed=_ok_embed(f"**{track.title}** will play next.", ctx))

    @commands.command()
    @help_meta(
        usage="`.radio <name>`",
        desc="Plays an internet radio station by name.",
        section="Playback",
        examples=[".radio lofi", ".radio chillhop"],
        params=[
            {"name": "name", "type": "str", "required": True, "desc": "Radio station name or search term."},
        ],
        note="Joins voice and starts streaming the radio station.",
    )
    async def radio(self, ctx: commands.Context, *, name: str = None) -> None:
        if not name:
            return await ctx.send(embed=_err_embed("gimme a station name. `.radio <name>`", ctx))
        if not await self._check_vc(ctx):
            return
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        if not player:
            try:
                channel = ctx.author.voice.channel
                player = await channel.connect(cls=wavelink.Player)
            except (AttributeError, discord.ClientException):
                return
            player.autoplay = wavelink.AutoPlayMode.disabled
            if not hasattr(player, "home"):
                player.home = ctx.channel
        url = f"https://de1.api.radio-browser.info/json/stations/byname/{urllib.parse.quote(name)}?limit=1&hidebroken=True&order=clickcount"
        session = await self._get_session()
        try:
            async with session.get(url) as r:
                if r.status != 200:
                    return await ctx.send(embed=_err_embed("radio lookup failed.", ctx))
                data = await r.json()
        except Exception:
            return await ctx.send(embed=_err_embed("radio lookup failed.", ctx))
        if not data:
            return await ctx.send(embed=_err_embed("no station found for that name.", ctx))
        station = data[0]
        stream_url = station.get("url_resolved") or station.get("url")
        if not stream_url:
            return await ctx.send(embed=_err_embed("station has no stream url.", ctx))
        try:
            result = await wavelink.Playable.search(stream_url)
        except Exception as e:
            log.warning("radio resolve error: %s", e)
            return await ctx.send(embed=_err_embed("couldn't play station.", ctx))
        if isinstance(result, list) and result:
            track = result[0]
        elif not isinstance(result, list):
            track = result
        else:
            return await ctx.send(embed=_err_embed("station returned nothing.", ctx))
        await player.queue.put_wait(track)
        if not player.playing:
            await player.play(player.queue.get())
        await ctx.send(embed=_ok_embed(f"playing **{station.get('name', name)}** {Neixoemojis.get('cd')}", ctx))

    @commands.command(aliases=["eq"])
    @help_meta(
        usage="`.exportqueue`",
        desc="DMs you the current queue as clickable links.",
        section="Controls",
        examples=[".exportqueue"],
        params=[],
        note="Each track is sent as a clickable link to the song on its source platform.",
    )
    async def exportqueue(self, ctx: commands.Context) -> None:
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        if not player or not player.current:
            return await ctx.send(embed=_err_embed("nothing playing.", ctx))
        lines = [f"**now playing:** [{player.current.title}]({player.current.uri})"]
        for i, t in enumerate(player.queue, 1):
            lines.append(f"**{i}.** [{t.title}]({t.uri})")
        content = "\n".join(lines)
        try:
            for chunk in [content[i:i+1900] for i in range(0, len(content), 1900)]:
                await ctx.author.send(chunk)
            await ctx.send(embed=_ok_embed("queue sent to your DMs.", ctx))
        except discord.Forbidden:
            await ctx.send(embed=_err_embed("couldn't DM you. check your privacy settings.", ctx))

    @commands.command()
    @help_meta(
        usage="`.stats`",
        desc="Shows session stats: tracks played, total time, and top requester.",
        section="Playback",
        examples=[".stats"],
        params=[],
        note="Stats reset when the bot leaves the voice channel.",
    )
    async def stats(self, ctx: commands.Context) -> None:
        if not ctx.guild:
            return
        guild_stats = self._session_stats.get(ctx.guild.id)
        if not guild_stats:
            return await ctx.send(embed=_err_embed("no stats for this session yet.", ctx))
        total_time = _fmt_time(guild_stats["total_ms"])
        top_rid = max(guild_stats["requesters"], key=guild_stats["requesters"].get, default=None) if guild_stats["requesters"] else None
        top_name = "nobody"
        if top_rid:
            member = ctx.guild.get_member(top_rid)
            top_name = member.display_name if member else str(top_rid)
        desc = (
            f"-# tracks played: **{guild_stats['tracks']}**\n"
            f"-# total time: **{total_time}**\n"
            f"-# top requester: **{top_name}** ({guild_stats['requesters'].get(top_rid, 0)} tracks)"
        )
        await ctx.send(embed=discord.Embed(description=desc, color=Neixocolor))

    @voteskip.error
    async def voteskip_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(embed=_err_embed("please wait 10 seconds before starting another vote.", ctx), delete_after=5)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
