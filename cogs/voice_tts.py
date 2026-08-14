"""
cogs/voice_tts.py  —  Free High-Fidelity Female Neural TTS Voice Engine (Beta)
"""

import asyncio
import logging
import os
import tempfile
from typing import Optional

import discord
from discord.ext import commands

from utils import DATA_DIR, get_embed_color, help_meta

log = logging.getLogger(__name__)

COG_META = {
    "category": "utility",
    "label": "Voice TTS",
    "desc": "Natural female neural text-to-speech engine for voice channels.",
}

# Curated high-fidelity female neural voices (100% free via Edge Neural TTS)
FEMALE_VOICES = {
    "ava": {"id": "en-US-AvaNeural", "name": "Ava (US)", "desc": "Warm, natural expressive female voice"},
    "jenny": {"id": "en-US-JennyNeural", "name": "Jenny (US)", "desc": "Clear, friendly conversational female voice"},
    "emma": {"id": "en-US-EmmaNeural", "name": "Emma (US)", "desc": "Gentle, soothing female voice"},
    "aria": {"id": "en-US-AriaNeural", "name": "Aria (US)", "desc": "Professional, confident female voice"},
    "sonia": {"id": "en-GB-SoniaNeural", "name": "Sonia (UK)", "desc": "Refined British female voice"},
    "maisie": {"id": "en-GB-MaisieNeural", "name": "Maisie (UK)", "desc": "Youthful British female voice"},
    "clara": {"id": "en-CA-ClaraNeural", "name": "Clara (CA)", "desc": "Crisp Canadian English female voice"},
    "nanami": {"id": "ja-JP-NanamiNeural", "name": "Nanami (JP)", "desc": "Natural Japanese anime female voice"},
}

DEFAULT_VOICE = "en-US-AvaNeural"


class VoiceTTS(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # guild_id -> voice_key
        self._guild_voices: dict[int, str] = {}
        # guild_id -> asyncio.Lock
        self._locks: dict[int, asyncio.Lock] = {}
        # guild_id -> asyncio.Task for idle disconnect
        self._idle_tasks: dict[int, asyncio.Task] = {}

    def _get_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._locks:
            self._locks[guild_id] = asyncio.Lock()
        return self._locks[guild_id]

    def _get_voice_id(self, guild_id: int) -> str:
        key = self._guild_voices.get(guild_id, "ava")
        return FEMALE_VOICES.get(key, {}).get("id", DEFAULT_VOICE)

    async def _schedule_idle_disconnect(self, guild: discord.Guild, timeout: int = 180):
        """Disconnects after timeout seconds of silence."""
        if guild.id in self._idle_tasks:
            self._idle_tasks[guild.id].cancel()

        async def _idle():
            try:
                await asyncio.sleep(timeout)
                vc = guild.voice_client
                if vc and vc.is_connected() and not vc.is_playing():
                    await vc.disconnect()
            except asyncio.CancelledError:
                pass

        self._idle_tasks[guild.id] = asyncio.create_task(_idle())

    @commands.group(name="tts", invoke_without_command=True)
    @help_meta(
        usage="`.tts <text>`",
        desc="Speaks your message in voice channel using high-fidelity natural female neural TTS.",
        section="Voice TTS",
        perm_tier="public",
        examples=[
            ".tts Hello everyone in the voice channel!",
            ".tts voice jenny",
            ".tts voices",
            ".tts stop",
        ],
        params=[
            {"name": "text", "type": "str", "required": True, "desc": "Message text for the bot to speak aloud in voice channel."},
        ],
        note="Requires you to be in a voice channel. 100% free neural speech with zero limits.",
    )
    async def tts(self, ctx: commands.Context, *, text: str = None):
        if ctx.guild is None:
            return await ctx.send("-# voice commands only work in servers")
        if not text:
            return await ctx.send("-# usage: `.tts <text>` · `.tts voice <name>` · `.tts voices` · `.tts stop`")

        if len(text) > 400:
            return await ctx.send("-# message too long (max 400 characters)")

        author = ctx.author
        if not author.voice or not author.voice.channel:
            return await ctx.send("-# you must be in a voice channel to use TTS")

        channel = author.voice.channel

        # Cancel any pending idle disconnect
        if ctx.guild.id in self._idle_tasks:
            self._idle_tasks[ctx.guild.id].cancel()

        lock = self._get_lock(ctx.guild.id)
        async with lock:
            voice_client: Optional[discord.VoiceClient] = ctx.guild.voice_client
            if voice_client is None:
                try:
                    voice_client = await channel.connect()
                except Exception as e:
                    return await ctx.send(f"-# failed to join voice channel: {str(e).lower()}")
            elif voice_client.channel.id != channel.id:
                try:
                    await voice_client.move_to(channel)
                except Exception as e:
                    return await ctx.send(f"-# failed to move to voice channel: {str(e).lower()}")

            voice_id = self._get_voice_id(ctx.guild.id)

            temp_path = None
            try:
                import edge_tts
                communicate = edge_tts.Communicate(text, voice_id)
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    temp_path = f.name

                await communicate.save(temp_path)

                # Wait if currently speaking
                while voice_client.is_playing():
                    await asyncio.sleep(0.2)

                done_event = asyncio.Event()

                def _after_play(err):
                    if err:
                        log.error(f"TTS playback error: {err}")
                    done_event.set()

                voice_client.play(discord.FFmpegPCMAudio(temp_path), after=_after_play)
                await done_event.wait()

            except Exception as e:
                log.exception("TTS generation/playback error")
                await ctx.send(f"-# tts error: {str(e).lower()}")
            finally:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

            # Reset idle disconnect timer
            await self._schedule_idle_disconnect(ctx.guild, timeout=120)

    @tts.command(name="voices")
    @help_meta(
        usage="`.tts voices`",
        desc="Lists all available female neural voice models.",
        section="Voice TTS",
        perm_tier="public",
        examples=[".tts voices"],
        params=[],
    )
    async def tts_voices(self, ctx: commands.Context):
        current_key = self._guild_voices.get(ctx.guild.id if ctx.guild else 0, "ava")
        lines = []
        for key, info in FEMALE_VOICES.items():
            active_badge = " **(active)**" if key == current_key else ""
            lines.append(f"• **`.tts voice {key}`** — {info['name']}: *{info['desc']}*{active_badge}")

        embed = discord.Embed(
            title="✦ Female Neural Voice Models (Beta)",
            description="\n".join(lines),
            color=get_embed_color(ctx.guild.id if ctx.guild else 0),
        )
        embed.set_footer(text="Powered by free High-Definition Neural Speech Engine")
        await ctx.send(embed=embed)

    @tts.command(name="voice")
    @help_meta(
        usage="`.tts voice <voice_name>`",
        desc="Switches the active female TTS voice for this server.",
        section="Voice TTS",
        perm_tier="public",
        examples=[
            ".tts voice ava",
            ".tts voice jenny",
            ".tts voice sonia",
            ".tts voice nanami",
        ],
        params=[
            {"name": "voice_name", "type": "str", "required": True, "desc": "Voice identifier (e.g., `ava`, `jenny`, `emma`, `sonia`, `nanami`)."},
        ],
    )
    async def tts_set_voice(self, ctx: commands.Context, name: str = None):
        if not name:
            return await self.tts_voices(ctx)

        clean = name.lower().strip()
        if clean not in FEMALE_VOICES:
            valid = ", ".join(f"`{k}`" for k in FEMALE_VOICES.keys())
            return await ctx.send(f"-# unknown voice `{clean}`. valid voices: {valid}")

        if ctx.guild:
            self._guild_voices[ctx.guild.id] = clean

        info = FEMALE_VOICES[clean]
        await ctx.send(f"-# switched tts voice to **{info['name']}** (*{info['desc']}*)")

    @tts.command(name="stop", aliases=["leave", "disconnect"])
    @help_meta(
        usage="`.tts stop`",
        desc="Stops current TTS playback and disconnects the bot from voice channel.",
        section="Voice TTS",
        perm_tier="public",
        examples=[".tts stop"],
        params=[],
    )
    async def tts_stop(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# voice commands only work in servers")
        vc: Optional[discord.VoiceClient] = ctx.guild.voice_client
        if not vc or not vc.is_connected():
            return await ctx.send("-# not connected to any voice channel")

        if vc.is_playing():
            vc.stop()

        await vc.disconnect()
        if ctx.guild.id in self._idle_tasks:
            self._idle_tasks[ctx.guild.id].cancel()

        await ctx.message.add_reaction("✓")


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceTTS(bot))
