from __future__ import annotations

import asyncio
import logging
import re

import aiohttp
import discord
from discord.ext import commands

from utils import (
    DATA_DIR,
    get_embed_color,
    help_meta,
    is_creator,
    is_owner_or_creator,
    load_json,
    save_json,
)

log = logging.getLogger(__name__)

PRESENCE_FILE = f"{DATA_DIR}/presence.json"

# ── cogs/profile.py ─────────────────────────────────────────────
COG_META = {
    "category": "profile",
    "label": "Profile",
    "desc": "Bot profile customization (creator only).",
    "owner": True,
}


# ── presence persistence ────────────────────────────────────────

_STATUS_MAP = {
    "online":    discord.Status.online,
    "idle":      discord.Status.idle,
    "away":      discord.Status.idle,
    "dnd":       discord.Status.dnd,
    "busy":      discord.Status.dnd,
    "invisible": discord.Status.invisible,
    "offline":   discord.Status.invisible,
}

_ACT_MAP = {
    "playing":    discord.ActivityType.playing,
    "listening":  discord.ActivityType.listening,
    "watching":   discord.ActivityType.watching,
    "competing":  discord.ActivityType.competing,
}

# default values match the original on_ready behavior
_DEFAULT = {
    "status": "dnd",
    "activity": {"type": "listening", "name": "discord.gg/seoulities"},
}

_EMOJI_RE = re.compile(r"<(a?):(\w+):(\d+)>")
_UNSET = object()  # sentinel for "don't touch"


def _build_activity(act: dict | None) -> discord.BaseActivity | None:
    if not act:
        return None
    t = (act.get("type") or "").lower()
    name = act.get("name") or ""
    if t == "custom":
        emoji = act.get("emoji")
        partial = None
        if emoji:
            m = _EMOJI_RE.fullmatch(emoji)
            if m:
                partial = discord.PartialEmoji(
                    animated=bool(m.group(1)), name=m.group(2), id=int(m.group(3))
                )
            else:
                partial = discord.PartialEmoji(name=emoji)
        return discord.CustomActivity(name=name or None, emoji=partial)
    if t in _ACT_MAP and name:
        return discord.Activity(type=_ACT_MAP[t], name=name)
    return None


async def load_saved_presence() -> tuple[discord.Status, discord.BaseActivity | None]:
    """Used by on_ready to restore persisted presence."""
    raw = load_json(PRESENCE_FILE) or _DEFAULT
    status = _STATUS_MAP.get(str(raw.get("status", "dnd")).lower(), discord.Status.dnd)
    activity = _build_activity(raw.get("activity"))
    # mirror into the in-memory state so subsequent commands can merge
    # against the actual current presence instead of trusting bot.activity
    # (which isn't reliably populated after change_presence calls).
    global _current_status, _current_activity
    async with _presence_lock:
        _current_status = status
        _current_activity = activity
    return status, activity


# ── in-memory current presence (source of truth for merging changes) ──
_current_status: discord.Status = discord.Status.dnd
_current_activity: discord.BaseActivity | None = None
_presence_lock: asyncio.Lock = asyncio.Lock()


async def _apply_presence(
    bot: commands.Bot,
    *,
    status: discord.Status | None = None,
    activity: discord.BaseActivity | None | _UNSET = _UNSET,
) -> None:
    """Apply a presence change preserving the half not being touched.
    `activity=_UNSET` (default) means leave activity alone; `activity=None`
    explicitly clears it."""
    global _current_status, _current_activity
    async with _presence_lock:
        if status is not None:
            _current_status = status
        if activity is not _UNSET:
            _current_activity = activity
        try:
            await bot.change_presence(status=_current_status, activity=_current_activity)
        except Exception as e:
            log.warning(f"failed to apply presence: {e}")


# ── status rotation engine (.rpc) ───────────────────────────────
RPC_FILE = f"{DATA_DIR}/rpc.json"
RPC_MIN_INTERVAL = 5     # discord allows 5 updates per 20s; this is the floor
RPC_DEFAULT_INTERVAL = 5
RPC_MAX_ENTRIES = 5      # one full cycle stays inside the rate-limit window


def _load_rpc() -> dict:
    raw = load_json(RPC_FILE) or {}
    return {
        "entries": list(raw.get("entries", [])),
        "interval": int(raw.get("interval", RPC_DEFAULT_INTERVAL)),
    }


def _save_rpc(state: dict) -> None:
    save_json(RPC_FILE, {
        "entries": list(state.get("entries", [])),
        "interval": int(state.get("interval", RPC_DEFAULT_INTERVAL)),
    })


class _RPCManager:
    """Auto-managed status rotation:
       0 entries → no activity
       1 entry  → that single status, no loop running
       2+       → cycles every `interval` seconds
       The user never starts/stops manually — entry count drives state."""

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.bot: commands.Bot | None = None
        self.idx: int = 0

    def attach(self, bot: commands.Bot) -> None:
        self.bot = bot
        if self.list_entries():
            self._kick()

    # ── state queries ────────────────────────────────────────
    def list_entries(self) -> list[dict]:
        return _load_rpc()["entries"]

    def get_interval(self) -> int:
        return _load_rpc()["interval"]

    # ── mutations ────────────────────────────────────────────
    def add_entry(self, entry: dict) -> int | None:
        state = _load_rpc()
        if len(state["entries"]) >= RPC_MAX_ENTRIES:
            return None  # at the cap
        state["entries"].append(entry)
        _save_rpc(state)
        self._kick()
        return len(state["entries"])

    def remove_entry(self, index: int) -> dict | None:
        """1-based remove. Returns the popped entry or None if invalid."""
        state = _load_rpc()
        if not (0 < index <= len(state["entries"])):
            return None
        removed = state["entries"].pop(index - 1)
        _save_rpc(state)
        if not state["entries"]:
            self._stop_and_clear()
        else:
            self._kick()
        return removed

    def clear_entries(self) -> int:
        state = _load_rpc()
        n = len(state["entries"])
        state["entries"] = []
        _save_rpc(state)
        self._stop_and_clear()
        return n

    def set_interval(self, seconds: int) -> int:
        state = _load_rpc()
        state["interval"] = max(RPC_MIN_INTERVAL, int(seconds))
        _save_rpc(state)
        # nothing to restart — the running loop reads the new value next tick
        return state["interval"]

    # ── lifecycle ────────────────────────────────────────────
    def _kick(self) -> None:
        """Cancel any running loop and start fresh based on current entries."""
        if not self.bot:
            return
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = None
        self.idx = 0
        if self.list_entries():
            self.task = asyncio.create_task(self._loop())

    def _stop_and_clear(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = None
        if self.bot:
            # clear the bot's activity AND the persisted "manual" activity
            # so the bot doesn't snap back to a stale value on next restart.
            # keep a reference so the task isn't garbage-collected mid-flight
            self._clear_task = asyncio.create_task(_apply_presence(self.bot, activity=None))
            _save_presence(activity=None)

    async def _loop(self) -> None:
        try:
            assert self.bot is not None
            await self.bot.wait_until_ready()
            while True:
                state = _load_rpc()
                entries = state["entries"]
                if not entries:
                    return
                idx = self.idx % len(entries)
                entry = entries[idx]
                try:
                    act = _build_activity(entry)
                    await _apply_presence(self.bot, activity=act)
                except Exception as e:
                    log.warning(f"rpc: failed to apply entry {idx}: {e}")
                if len(entries) == 1:
                    # single entry: set once, no further ticks needed
                    return
                self.idx = idx + 1
                await asyncio.sleep(max(RPC_MIN_INTERVAL, state["interval"]))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning(f"rpc loop crashed: {e}")
        finally:
            # only clear if this is still the current task — _kick() may have
            # replaced us while we were shutting down
            if self.task is asyncio.current_task():
                self.task = None


rpc = _RPCManager()


def _save_presence(status: str | None = None, activity: dict | None | _UNSET = _UNSET) -> dict:
    """Merge-save the presence file. Pass `activity=None` to clear it."""
    cur = load_json(PRESENCE_FILE) or dict(_DEFAULT)
    if status is not None:
        cur["status"] = status
    if activity is not _UNSET:
        cur["activity"] = activity
    save_json(PRESENCE_FILE, cur)
    return cur


# ── helpers for image inputs ────────────────────────────────────

_IMAGE_MAX_BYTES = 10 * 1024 * 1024

async def _resolve_image_bytes(ctx: commands.Context, source: str | None) -> bytes | None:
    """Get image bytes from an attachment, a URL string, or None if neither."""
    if ctx.message.attachments:
        att = ctx.message.attachments[0]
        if att.size > _IMAGE_MAX_BYTES:
            return None
        return await att.read()
    if not source or not source.startswith("https://"):
        return None
    if len(source) > 2000:
        return None
    async with aiohttp.ClientSession() as s, s.get(source, timeout=aiohttp.ClientTimeout(total=15)) as r:
        if r.status != 200:
            raise RuntimeError(f"download failed: HTTP {r.status}")
        data = await r.read()
        if len(data) > _IMAGE_MAX_BYTES:
            raise RuntimeError(f"file too large ({len(data)} bytes)")
        return data


def _creator_only():
    async def predicate(ctx: commands.Context):
        if is_creator(ctx.author.id):
            return True
        await ctx.send("-# creator only")
        return False
    return commands.check(predicate)


# ── the cog ─────────────────────────────────────────────────────

class ProfileCog(commands.Cog, name="Profile"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Re-sync the in-memory presence state from disk on every cog load
        # (initial load and any .reload profile). on_ready also does this
        # the first time the bot logs in.
        await load_saved_presence()
        # Attach RPC manager so it can auto-resume rotation from disk if
        # entries were saved last run.
        rpc.attach(self.bot)

    async def cog_unload(self):
        # Don't leave the RPC task running against a stale bot reference
        # if the cog is unloaded/reloaded — _kick() picks it back up.
        if rpc.task and not rpc.task.done():
            rpc.task.cancel()
        rpc.task = None
        clear_task = getattr(rpc, "_clear_task", None)
        if clear_task and not clear_task.done():
            clear_task.cancel()

    @help_meta(
        usage="`.presence <online|idle|dnd|invisible>`",
        desc="Sets the bot's online status.",
        owner=True,
        examples=[".presence online", ".presence dnd", ".presence idle"],
        params=[
            {"name": "status", "type": "str", "required": True, "desc": "One of: online, idle, dnd, invisible."},
        ],
        note="Owner only. Your current presence preference is saved and restored on restart.",
    )
    @commands.command(name="presence")
    @_creator_only()
    async def presence(self, ctx, value: str = None):
        if not value or value.lower() not in _STATUS_MAP:
            return await ctx.send(
                "-# usage: `.presence <online|idle|dnd|invisible>`"
            )
        key = value.lower()
        await _apply_presence(self.bot, status=_STATUS_MAP[key])
        _save_presence(status=key)
        await ctx.message.add_reaction("<:7079verifiedblacksimplified:1255031445806780467>")

    @help_meta(
        usage="`.activity <playing|listening|watching|competing> <text>`  ·  `.activity none`",
        desc="Sets or clears the bot's activity status.",
        owner=True,
        examples=[".activity playing with fire", ".activity listening to music", ".activity none"],
        params=[
            {"name": "type", "type": "str", "required": True, "desc": "Activity type: playing, listening, watching, competing, or none (to clear)."},
            {"name": "text", "type": "str", "required": False, "desc": "The activity text to display."},
        ],
        note="Owner only. Use `.activity none` to clear the current activity.",
    )
    @commands.command(name="activity")
    @_creator_only()
    async def activity(self, ctx, kind: str = None, *, text: str = ""):
        if not kind:
            return await ctx.send(
                "-# usage: `.activity <playing|listening|watching|competing> <text>` "
                "or `.activity none` to clear"
            )
        if kind.lower() in ("none", "clear", "off"):
            await _apply_presence(self.bot, activity=None)
            _save_presence(activity=None)
            await ctx.message.add_reaction("<:redlotus:1263556248310386800>")
            return
        if kind.lower() not in _ACT_MAP:
            return await ctx.send(
                "-# type must be one of: playing, listening, watching, competing"
            )
        if not text:
            return await ctx.send("-# you need to give me text to display")
        act = discord.Activity(type=_ACT_MAP[kind.lower()], name=text)
        await _apply_presence(self.bot, activity=act)
        _save_presence(activity={"type": kind.lower(), "name": text})
        await ctx.message.add_reaction("<:7079verifiedblacksimplified:1255031445806780467>")

    @help_meta(
        usage="`.pfp [url|attachment|reset]`",
        desc="Changes the bot's avatar.",
        owner=True,
        examples=[".pfp https://i.imgur.com/abc.png", ".pfp reset"],
        params=[
            {"name": "source", "type": "str", "required": False, "desc": "Image URL, attachment, or `reset` to clear."},
        ],
        note="Owner only. Discord rate-limits avatar changes to ~2 per 10 minutes.",
    )
    @commands.command(name="pfp", aliases=["avatar"])
    @_creator_only()
    @commands.cooldown(2, 600, commands.BucketType.default)
    async def pfp(self, ctx, *, source: str = None):
        # discord rate-limits avatar changes to roughly 2 per 10 minutes;
        # the cooldown above mirrors that so we surface a friendly message
        # instead of letting discord 429 us mid-call.
        if source and source.lower() == "reset":
            try:
                await self.bot.user.edit(avatar=None)
                return await ctx.send("-# avatar cleared")
            except discord.HTTPException as e:
                return await ctx.send(f"-# failed: {e}")
        try:
            data = await _resolve_image_bytes(ctx, source)
        except Exception as e:
            return await ctx.send(f"-# couldn't download: {e}")
        if not data:
            return await ctx.send("-# attach an image or pass a URL")
        try:
            await self.bot.user.edit(avatar=data)
            await ctx.message.add_reaction("<:7079verifiedblacksimplified:1255031445806780467>")
        except discord.HTTPException as e:
            await ctx.send(f"-# discord rejected it: {e}")

    @help_meta(
        usage="`.banner [url|attachment|reset]`",
        desc="Changes the bot's banner.",
        owner=True,
        examples=[".banner https://i.imgur.com/abc.png", ".banner reset"],
        params=[
            {"name": "source", "type": "str", "required": False, "desc": "Image URL, attachment, or `reset` to clear."},
        ],
        note="Owner only. Same rate-limit as avatar: ~2 per 10 minutes.",
    )
    @commands.command(name="banner")
    @_creator_only()
    @commands.cooldown(2, 600, commands.BucketType.default)
    async def banner(self, ctx, *, source: str = None):
        # same rate-limit as avatar: 2 per 10 min.
        if source and source.lower() == "reset":
            try:
                await self.bot.user.edit(banner=None)
                return await ctx.send("-# banner cleared")
            except discord.HTTPException as e:
                return await ctx.send(f"-# failed: {e}")
        try:
            data = await _resolve_image_bytes(ctx, source)
        except Exception as e:
            return await ctx.send(f"-# couldn't download: {e}")
        if not data:
            return await ctx.send("-# attach an image or pass a URL")
        try:
            await self.bot.user.edit(banner=data)
            await ctx.message.add_reaction("<:7079verifiedblacksimplified:1255031445806780467>")
        except discord.HTTPException as e:
            await ctx.send(f"-# discord rejected it: {e}")

    @help_meta(
        usage="`.setavatar [url|attachment|reset]`",
        desc="Changes the bot's server-specific avatar.",
        owner=True,
        examples=[".setavatar https://i.imgur.com/abc.png", ".setavatar reset"],
        params=[
            {"name": "source", "type": "str", "required": False, "desc": "Image URL, attachment, or `reset` to clear."},
        ],
        note="Server Owner / Bot Creator only. Discord rate-limits apply.",
    )
    @commands.command(name="setavatar")
    @commands.guild_only()
    @commands.check(is_owner_or_creator)
    @commands.cooldown(2, 600, commands.BucketType.default)
    async def setavatar(self, ctx, *, source: str = None):
        if source and source.lower() == "reset":
            try:
                route = discord.http.Route('PATCH', '/guilds/{guild_id}/members/@me', guild_id=ctx.guild.id)
                await self.bot.http.request(route, json={'avatar': None})
                await ctx.message.add_reaction("<:redlotus:1263556248310386800>")
                return
            except discord.HTTPException as e:
                return await ctx.send(f"-# failed: {e}")
        try:
            data = await _resolve_image_bytes(ctx, source)
        except Exception as e:
            return await ctx.send(f"-# couldn't download: {e}")
        if not data:
            return await ctx.send("-# attach an image or pass a URL")
        try:
            b64 = discord.utils._bytes_to_base64_data(data)
            route = discord.http.Route('PATCH', '/guilds/{guild_id}/members/@me', guild_id=ctx.guild.id)
            await self.bot.http.request(route, json={'avatar': b64})
            await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        except discord.HTTPException as e:
            await ctx.send(f"-# discord rejected it: {e}")

    @help_meta(
        usage="`.removeavatar`",
        desc="Removes the server-specific bot avatar (falls back to the global one).",
        owner=True,
        section="Bot Profile",
        examples=[".removeavatar"],
        params=[],
        note="Owner only.",
    )
    @commands.command(name="removeavatar")
    @commands.guild_only()
    @commands.check(is_owner_or_creator)
    async def removeavatar(self, ctx):
        await self.setavatar(ctx, source="reset")

    @help_meta(
        usage="`.setbanner [url|attachment|reset]`",
        desc="Changes the bot's server-specific banner.",
        owner=True,
        examples=[".setbanner https://i.imgur.com/abc.png", ".setbanner reset"],
        params=[
            {"name": "source", "type": "str", "required": False, "desc": "Image URL, attachment, or `reset` to clear."},
        ],
        note="Server Owner / Bot Creator only. Discord rate-limits apply.",
    )
    @commands.command(name="setbanner")
    @commands.guild_only()
    @commands.check(is_owner_or_creator)
    @commands.cooldown(2, 600, commands.BucketType.default)
    async def setbanner(self, ctx, *, source: str = None):
        if source and source.lower() == "reset":
            try:
                route = discord.http.Route('PATCH', '/guilds/{guild_id}/members/@me', guild_id=ctx.guild.id)
                await self.bot.http.request(route, json={'banner': None})
                await ctx.message.add_reaction("<:redlotus:1263556248310386800>")
                return
            except discord.HTTPException as e:
                return await ctx.send(f"-# failed: {e}")
        try:
            data = await _resolve_image_bytes(ctx, source)
        except Exception as e:
            return await ctx.send(f"-# couldn't download: {e}")
        if not data:
            return await ctx.send("-# attach an image or pass a URL")
        try:
            b64 = discord.utils._bytes_to_base64_data(data)
            route = discord.http.Route('PATCH', '/guilds/{guild_id}/members/@me', guild_id=ctx.guild.id)
            await self.bot.http.request(route, json={'banner': b64})
            await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        except discord.HTTPException as e:
            await ctx.send(f"-# discord rejected it: {e}")

    @help_meta(
        usage="`.username <name>`",
        desc="Changes the bot's username.",
        owner=True,
        examples=[".username MyBot"],
        params=[
            {"name": "name", "type": "str", "required": True, "desc": "New username (2-32 characters)."},
        ],
        note="Owner only. Discord rate-limits username changes to ~2 per 2 hours.",
    )
    @commands.command(name="username", aliases=["botname"])
    @_creator_only()
    @commands.cooldown(2, 7200, commands.BucketType.default)
    async def username(self, ctx, *, name: str = None):
        # discord rate-limits username changes to ~2 per 2 hours; the
        # cooldown above mirrors that so the bot doesn't 429 itself.
        if not name or not name.strip():
            return await ctx.send("-# usage: `.username <new name>`")
        name = name.strip()
        if not (2 <= len(name) <= 32):
            return await ctx.send("-# username must be 2–32 characters")
        try:
            await self.bot.user.edit(username=name)
            await ctx.message.add_reaction("<:7079verifiedblacksimplified:1255031445806780467>")
        except discord.HTTPException as e:
            await ctx.send(f"-# discord rejected it: {e}")

    # ── status rotation (.rpc) ───────────────────────────────
    @help_meta(
        usage="`.rpc`  ·  `.rpc add [emoji] <text>`  ·  `.rpc remove <n>`  ·  `.rpc clear`  ·  `.rpc interval <secs>`",
        desc="Auto-managed status rotation. 1 entry = static, 2+ = cycles. Max 5 entries, min 5s interval.",
        owner=True,
        examples=[".rpc", ".rpc add 🌟 playing with fire", ".rpc remove 2", ".rpc interval 30", ".rpc clear"],
        params=[
            {"name": "action", "type": "str", "required": False, "desc": "add, remove, clear, interval, or omit to show current rotation."},
            {"name": "emoji", "type": "str", "required": False, "desc": "Optional emoji (for add action)."},
            {"name": "text", "type": "str", "required": False, "desc": "Status text (for add action)."},
            {"name": "n", "type": "int", "required": False, "desc": "Index number (for remove action)."},
            {"name": "seconds", "type": "int", "required": False, "desc": "Interval in seconds (for interval action, min 5)."},
        ],
        note="Owner only. Statuses cycle in order. Max 5 entries, minimum 5s interval.",
    )
    @commands.group(name="rpc", invoke_without_command=True)
    @_creator_only()
    async def rpc_group(self, ctx):
        """Default invocation: show the rotation list + state."""
        entries = rpc.list_entries()
        interval = rpc.get_interval()

        if not entries:
            return await ctx.send(
                "-# no entries yet. add one with `.rpc add [emoji] <text>`"
            )

        lines = []
        for i, e in enumerate(entries, start=1):
            n = e.get("name", "")
            em = e.get("emoji")
            label = f"{em} {n}" if em else n
            lines.append(f"`{i}.` {label}")

        if len(entries) == 1:
            state = "🟢 static"
        else:
            state = f"🟢 cycling every `{interval}s`"

        embed = discord.Embed(
            title="status rotation",
            description="\n".join(lines),
            color=get_embed_color(ctx.guild.id) if ctx.guild else 0xFF0000,
        )
        embed.set_footer(text=f"{state} · {len(entries)}/{RPC_MAX_ENTRIES} entries")
        await ctx.send(embed=embed)

    @help_meta(
        usage=".rpc add [emoji] <text>",
        desc="Adds a custom-status entry for rotation.",
        section="RPC",
        owner=True,
        examples=[".rpc add 🌟 playing with fire", ".rpc add listening to music"],
        params=[
            {"name": "emoji", "type": "str", "required": False, "desc": "Optional emoji prefix."},
            {"name": "text", "type": "str", "required": True, "desc": "Status text."},
        ],
        note="Owner only. Max 5 entries.",
    )
    @rpc_group.command(name="add")
    @_creator_only()
    async def rpc_add(self, ctx, *, text: str = ""):
        """`.rpc add [emoji] <text>` — adds a custom-status entry.
        Emoji can be unicode (rendered) or custom guild emoji (often won't render)."""
        if not text.strip():
            return await ctx.send("-# usage: `.rpc add [emoji] <text>`")

        text = text.strip()
        emoji_payload = None
        display = text

        # custom guild emoji at the start? parse + warn that it likely won't render
        m = re.match(r"^(<a?:\w+:\d+>)\s*(.*)$", display)
        if m:
            emoji_payload = m.group(1)
            display = m.group(2).strip()
            await ctx.send(
                "-# fyi: discord rarely renders custom guild emoji in a bot's "
                "status. unicode emoji (🎵) shows reliably."
            )
        # unicode emoji stays inside `display` (the name field) where it
        # always renders for bot custom statuses.

        n = rpc.add_entry({"type": "custom", "name": display, "emoji": emoji_payload})
        if n is None:
            return await ctx.send(
                f"-# at the cap of {RPC_MAX_ENTRIES} entries — remove one first"
            )
        await ctx.send(f"-# added entry `{n}` ({n}/{RPC_MAX_ENTRIES})")

    @help_meta(
        usage=".rpc remove <n>",
        desc="Removes a custom-status entry by its index number.",
        section="RPC",
        owner=True,
        examples=[".rpc remove 2"],
        params=[
            {"name": "index", "type": "int", "required": True, "desc": "Index number of the entry to remove."},
        ],
        note="Owner only. Use `.rpc` without arguments to see the list with indices.",
    )
    @rpc_group.command(name="remove", aliases=["rm", "del", "delete"])
    @_creator_only()
    async def rpc_remove(self, ctx, index: int = None):
        if index is None:
            return await ctx.send("-# `.rpc remove <n>` — see `.rpc` for indices")
        removed = rpc.remove_entry(index)
        if not removed:
            return await ctx.send(f"-# no entry at index `{index}`")
        await ctx.message.add_reaction("<:redlotus:1263556248310386800>")

    @help_meta(
        usage=".rpc clear",
        desc="Removes all RPC entries and stops rotation.",
        section="RPC",
        owner=True,
        examples=[".rpc clear"],
        params=[],
        note="Owner only. This cannot be undone.",
    )
    @rpc_group.command(name="clear")
    @_creator_only()
    async def rpc_clear(self, ctx):
        n = rpc.clear_entries()
        await ctx.send(f"-# cleared {n} entries · status reset")

    @help_meta(
        usage=".rpc interval [seconds]",
        desc="Sets or checks the interval for status rotation cycling.",
        section="RPC",
        owner=True,
        examples=[".rpc interval 30", ".rpc interval 120"],
        params=[
            {"name": "seconds", "type": "int", "required": False, "desc": "Interval in seconds (min 5). Omit to check current interval."},
        ],
        note="Owner only.",
    )
    @rpc_group.command(name="interval")
    @_creator_only()
    async def rpc_interval(self, ctx, seconds: int = None):
        if seconds is None:
            return await ctx.send(
                f"-# current interval: `{rpc.get_interval()}s` "
                f"(min `{RPC_MIN_INTERVAL}s` — discord allows 5 status updates per 20s)"
            )
        if seconds < RPC_MIN_INTERVAL:
            return await ctx.send(
                f"-# minimum is `{RPC_MIN_INTERVAL}s` "
                "(discord rate limit: 5 updates per 20s)"
            )
        actual = rpc.set_interval(seconds)
        await ctx.send(f"-# interval set to `{actual}s`")


async def setup(bot: commands.Bot):
    await bot.add_cog(ProfileCog(bot))
