from __future__ import annotations

import logging
import time
from typing import List, Optional, Tuple, cast

import discord
import wavelink
from discord.ui import Button, Select, View

from cogs.music_helpers import (
    GENRE_MAP,
    Neixocolor,
    _err_embed,
    _fmt_time,
    _ok_embed,
    _spotify,
    log,
)

# ─────────────────────────────────────────────────────────────
# NOW-PLAYING VIEW (prev / pause-toggle / next)
# ─────────────────────────────────────────────────────────────

class NowPlayingView(View):
    def __init__(self, cog: "Music", player: wavelink.Player) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.player = player
        self.message: Optional[discord.Message] = None
        self._paused = False
        self._lyrics_data: Optional[Tuple[str, str, str]] = None
        self._synced_lines: Optional[List[Tuple[int, str]]] = None
        self._lyrics_cooldowns: dict[int, float] = {}
        self._last_lyrics_msg_id: Optional[int] = None

    @discord.ui.button(label="⏮", style=discord.ButtonStyle.gray)
    async def prev_btn(self, interaction: discord.Interaction, button: Button) -> None:
        await interaction.response.defer()
        player = self.player
        if not player or not player.current:
            return
        position_s = player.position // 1000
        history = self.cog._history.get(player.guild.id)

        if position_s >= 10 or not history:
            await player.seek(0)
            return

        prev_track = history[-1]
        history.pop()

        self.cog._prev_pressed.add(player.guild.id)

        if player.current:
            player.queue.put_at(0, player.current)

        await player.play(prev_track, replace=True)

    @discord.ui.button(label="⏸", style=discord.ButtonStyle.gray)
    async def pause_btn(self, interaction: discord.Interaction, button: Button) -> None:
        await interaction.response.defer()
        player = self.player

        if player.paused:
            await player.pause(False)
            button.label = "⏸"
            self._paused = False
            title = player.current.title if player.current else "playing"
            await self.cog._update_vc_status(player.channel.id, f"{title} | Neixo")
        else:
            await player.pause(True)
            button.label = "▶"
            self._paused = True
            await self.cog._update_vc_status(player.channel.id, "paused | Neixo")

        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="⏭", style=discord.ButtonStyle.gray)
    async def next_btn(self, interaction: discord.Interaction, button: Button) -> None:
        await interaction.response.defer()
        await self.player.skip(force=True)

    @discord.ui.button(emoji="<a:butterfly:1413057472213680148>", style=discord.ButtonStyle.gray, disabled=True)
    async def lyrics_btn(self, interaction: discord.Interaction, button: Button) -> None:
        now = time.monotonic()
        user_id = interaction.user.id
        last = self._lyrics_cooldowns.get(user_id, 0)
        if now - last < 10:
            await interaction.response.send_message("Please wait 10 seconds.", ephemeral=True)
            return
        self._lyrics_cooldowns[user_id] = now

        if not self._lyrics_data:
            await interaction.response.send_message("Lyrics not available.", ephemeral=True)
            return

        await interaction.response.defer()

        if self._last_lyrics_msg_id:
            try:
                old_msg = await interaction.channel.fetch_message(self._last_lyrics_msg_id)
                await old_msg.delete()
            except (discord.NotFound, discord.HTTPException):
                pass
            self._last_lyrics_msg_id = None

        if self._synced_lines and self.player.current:
            guild_id = self.player.guild.id
            if guild_id in self.cog._live_tasks and not self.cog._live_tasks[guild_id].done():
                seek_to = max(0, int(self.player.position) - 5000)
                await self.player.seek(seek_to)
            await self.cog._start_live_lyrics(interaction.channel, self.player, self._synced_lines, self.player.current)
            return

        artist, title, lyrics = self._lyrics_data
        msg = await self.cog._send_lyrics_to_channel(interaction.channel, lyrics, artist, title, author_id=user_id)
        self._last_lyrics_msg_id = msg.id

    async def deactivate(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

# ─────────────────────────────────────────────────────────────
# SOUNDCLOUD RETRY VIEW
# ─────────────────────────────────────────────────────────────

class SCRetryView(View):
    def __init__(self, cog: "Music", ctx, query: str) -> None:
        super().__init__(timeout=30)
        self.cog = cog
        self.ctx = ctx
        self.query = query

    @discord.ui.button(label="↺ Retry with SoundCloud", style=discord.ButtonStyle.gray)
    async def retry_btn(self, interaction: discord.Interaction, button: Button) -> None:
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        await interaction.response.defer()
        player: wavelink.Player = cast(wavelink.Player, self.ctx.voice_client)
        tracks = await wavelink.Playable.search(self.query, source="scsearch")
        if not tracks:
            await interaction.followup.send(embed=_err_embed("SoundCloud also came up empty.", self.ctx), ephemeral=True)
            self.stop()
            return
        track = tracks[0]
        await player.queue.put_wait(track)
        if not player.playing:
            await player.play(player.queue.get())
        await interaction.followup.send(embed=_ok_embed(f"added **{track.title}** via SoundCloud.", self.ctx), ephemeral=True)
        self.stop()

# ─────────────────────────────────────────────────────────────
# LOOP SELECT VIEW
# ─────────────────────────────────────────────────────────────

class LoopSelect(Select):
    def __init__(self, player: wavelink.Player, author_id: int) -> None:
        self.player = player
        self.author_id = author_id
        options = [
            discord.SelectOption(label="Track", description="Loop the current track", value="loop"),
            discord.SelectOption(label="Queue", description="Loop the whole queue", value="loop_all"),
            discord.SelectOption(label="Off", description="Disable loop", value="normal"),
        ]
        super().__init__(placeholder="Select loop mode", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        new_mode = getattr(wavelink.QueueMode, self.values[0])
        self.player.queue.mode = new_mode
        labels = {
            wavelink.QueueMode.loop: "looping **track**",
            wavelink.QueueMode.loop_all: "looping **queue**",
            wavelink.QueueMode.normal: "loop **disabled**",
        }
        await interaction.response.edit_message(embed=_ok_embed(labels[new_mode], interaction), view=self.view)

class LoopView(View):
    def __init__(self, player: wavelink.Player, author_id: int) -> None:
        super().__init__(timeout=60)
        self.message: Optional[discord.Message] = None
        self.add_item(LoopSelect(player, author_id))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException as e:
                log.warning("LoopView.on_timeout: %s", e)

# ─────────────────────────────────────────────────────────────
# SIMILAR TRACKS VIEW
# ─────────────────────────────────────────────────────────────

class SimilarMenu(Select):
    def __init__(self, tracks: list, player: wavelink.Player, author_id: int) -> None:
        self.player = player
        self.author_id = author_id
        self.tracks_map = {t["identifier"]: t for t in tracks}
        options = [discord.SelectOption(label=t["title"][:100], value=t["identifier"]) for t in tracks]
        super().__init__(placeholder="Pick tracks to add to queue", max_values=7, min_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        added = []
        for tid in self.values:
            info = self.tracks_map.get(tid)
            if not info:
                continue
            try:
                results = await wavelink.Playable.search(info["url"])
            except Exception as e:
                log.warning("SimilarMenu search error: %s", e)
                continue
            if results:
                await self.player.queue.put_wait(results[0])
                added.append(info["title"])
        await interaction.response.send_message(
            embed=_ok_embed(f"Added {len(added)} track(s):\n" + "\n".join(f"- {t}" for t in added), interaction),
            ephemeral=True,
        )
        self.view.stop()

class SimilarView(View):
    def __init__(self, tracks: list, player: wavelink.Player, author_id: int) -> None:
        super().__init__(timeout=60)
        self.add_item(SimilarMenu(tracks, player, author_id))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True

# ─────────────────────────────────────────────────────────────
# QUEUE PAGINATOR VIEW
# ─────────────────────────────────────────────────────────────

class QueueView(View):
    def __init__(self, ctx, player: wavelink.Player) -> None:
        super().__init__(timeout=60)
        self.ctx = ctx
        self.player = player
        self.page = 0
        self.per_page = 10
        self.message: Optional[discord.Message] = None
        self._sync_queue()
        self._build_buttons()

    def _sync_queue(self) -> None:
        self.queue = list(self.player.queue)
        self.total_pages = max(1, (len(self.queue) + self.per_page - 1) // self.per_page)

    def _build_buttons(self) -> None:
        self.clear_items()
        if self.total_pages > 1:
            self.add_item(self.prev_btn)
            self.add_item(self.next_btn)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.gray)
    async def prev_btn(self, interaction: discord.Interaction, button: Button) -> None:
        try:
            if interaction.user != self.ctx.author:
                return await interaction.response.send_message("Not your command.", ephemeral=True)
            self.page = max(0, self.page - 1)
            self._update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        except Exception as e:
            log.warning("QueueView prev_btn error: %s", e)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.gray)
    async def next_btn(self, interaction: discord.Interaction, button: Button) -> None:
        try:
            if interaction.user != self.ctx.author:
                return await interaction.response.send_message("Not your command.", ephemeral=True)
            self.page = min(self.total_pages - 1, self.page + 1)
            self._update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        except Exception as e:
            log.warning("QueueView next_btn error: %s", e)

    def _update_buttons(self) -> None:
        for child in self.children:
            if getattr(child, "label", None) == "◀":
                child.disabled = self.page == 0
            elif getattr(child, "label", None) == "▶":
                child.disabled = self.page >= self.total_pages - 1

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(color=Neixocolor)
        try:
            current = self.player.current
            if current:
                embed.add_field(name="now playing", value=f"[{current.title}]({current.uri})", inline=False)
            else:
                embed.add_field(name="now playing", value="nothing playing", inline=False)
            total_ms = sum(t.length for t in self.queue)
            loop_mode = {
                wavelink.QueueMode.loop: "🔂 track",
                wavelink.QueueMode.loop_all: "🔁 queue",
                wavelink.QueueMode.normal: "➡ off",
            }.get(self.player.queue.mode, "off")
        except Exception:
            total_ms = 0
            loop_mode = "unknown"
            embed.add_field(name="now playing", value="[unavailable]", inline=False)

        start = self.page * self.per_page
        chunk = self.queue[start : start + self.per_page]
        if chunk:
            desc = ""
            for i, t in enumerate(chunk, start=start + 1):
                line = f"{i}. [{t.title}]({t.uri})\n"
                if len(desc) + len(line) > 1024:
                    desc += "…and more."
                    break
                desc += line
            embed.add_field(name="up next", value=desc, inline=False)
        else:
            embed.add_field(name="up next", value="queue is empty.", inline=False)

        embed.set_footer(
            text=f"page {self.page + 1}/{self.total_pages} | "
                 f"total: {_fmt_time(total_ms)} | loop: {loop_mode}"
        )
        return embed

    async def on_timeout(self) -> None:
        try:
            for child in self.children:
                child.disabled = True
            if self.message:
                await self.message.edit(view=self)
        except discord.HTTPException:
            pass
        except Exception as e:
            log.warning("QueueView.on_timeout error: %s", e)

# ─────────────────────────────────────────────────────────────
# LYRICS PAGINATION VIEW
# ─────────────────────────────────────────────────────────────

class LyricsPaginationView(View):
    def __init__(self, author_id: int, pages: list, *, title: str, artist: str):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.pages = pages
        self.title = title
        self.artist = artist
        self.current_page = 0
        self.message: Optional[discord.Message] = None
        self._update_buttons()

    def _update_buttons(self):
        self.clear_items()
        if len(self.pages) > 1:
            self.prev_btn.disabled = self.current_page == 0
            self.next_btn.disabled = self.current_page >= len(self.pages) - 1
            self.add_item(self.prev_btn)
            self.add_item(self.next_btn)
        self.add_item(self.delete_btn)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.gray)
    async def prev_btn(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.current_page = max(0, self.current_page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.gray)
    async def next_btn(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="🗑️", style=discord.ButtonStyle.red)
    async def delete_btn(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Not your command.", ephemeral=True)
        await interaction.response.defer()
        if self.message:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass
        self.stop()

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"<a:butterfly:1413057472213680148> Lyrics for {self.title}",
            description="\n".join(self.pages[self.current_page]),
            color=Neixocolor,
        )
        embed.set_footer(text=f"Page {self.current_page + 1}/{len(self.pages)}")
        return embed

# ─────────────────────────────────────────────────────────────
# GENRE VIEW
# ─────────────────────────────────────────────────────────────

class GenreSelect(Select):
    def __init__(self, cog: "Music", ctx) -> None:
        self.cog = cog
        self.ctx = ctx
        options = [
            discord.SelectOption(label="Phonk", value="phonk", description="Dark trap phonk hits"),
            discord.SelectOption(label="Hindi Romantic", value="hindi_romantic", description="Love & feels"),
            discord.SelectOption(label="Hindi Sad", value="hindi_sad", description="Breakup & sad vibes"),
            discord.SelectOption(label="English", value="english", description="Top English picks"),
            discord.SelectOption(label="Viral", value="viral", description="Internet trending songs"),
        ]
        super().__init__(placeholder="Choose a genre", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        genre = self.values[0]
        playlist_id = GENRE_MAP.get(genre)
        if not playlist_id:
            return await interaction.response.send_message("Invalid genre.", ephemeral=True)
        await interaction.response.defer()
        player: wavelink.Player = self.ctx.voice_client
        try:
            names = await _spotify.get_playlist_tracks(playlist_id)
        except Exception as e:
            log.warning("Spotify genre fetch error: %s", e)
            return await interaction.followup.send("Couldn't reach Spotify API.")
        if not names:
            return await interaction.followup.send("No tracks found on Spotify.")
        names = names[:10]
        added = 0
        for name in names:
            try:
                results = await self.cog._yt_search_with_retry(name, source="ytsearch")
                if results:
                    await player.queue.put_wait(results[0])
                    added += 1
            except Exception as e:
                log.warning("GenreSelect search error for %r: %s", name, e)
        if not player.playing and not player.queue.is_empty:
            await player.play(player.queue.get())
        await interaction.followup.send(
            embed=_ok_embed(f"Added **{added}** `{genre.replace('_', ' ').title()}` tracks to the queue.", interaction)
        )

class GenreView(View):
    def __init__(self, cog: "Music", ctx) -> None:
        super().__init__(timeout=60)
        self.add_item(GenreSelect(cog, ctx))
