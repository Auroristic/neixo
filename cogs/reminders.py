import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands

from utils import load_json, save_json, get_embed_color, DATA_DIR, help_meta

log = logging.getLogger(__name__)

REMINDERS_FILE = f"{DATA_DIR}/reminders.json"
BIRTHDAYS_FILE = f"{DATA_DIR}/birthdays.json"

# ── cogs/reminders.py ───────────────────────────────────────────
COG_META = {
    "category": "general",
    "label": "General",
    "desc": "Core utility and reaction commands.",
}

# ── time parsing ────────────────────────────────────────────────

_REL_RE = re.compile(r"^(\d+)([smhdw])$", re.IGNORECASE)
_REL_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

# absolute datetime formats for `.remind`
_DT_FMTS = [
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d",
    "%Y-%m-%d",
]
# birthday formats for `.bday` — date only, year optional
_BDAY_FMTS = ["%Y/%m/%d", "%Y-%m-%d", "%m/%d", "%m-%d"]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_when(s: str) -> Optional[datetime]:
    """Parse `5m`, `2h`, `2026/11/08`, `2026/11/08 14:30` etc → UTC datetime."""
    s = s.strip()
    m = _REL_RE.match(s)
    if m:
        n = int(m.group(1))
        u = m.group(2).lower()
        return _now_utc() + timedelta(seconds=n * _REL_UNITS[u])
    for fmt in _DT_FMTS:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_bday(s: str) -> Optional[str]:
    """Parse a date → 'MM-DD' string. Year is ignored (yearly recurring)."""
    s = s.strip()
    for fmt in _BDAY_FMTS:
        try:
            return datetime.strptime(s, fmt).strftime("%m-%d")
        except ValueError:
            continue
    return None


def _format_delta(d: timedelta) -> str:
    secs = int(d.total_seconds())
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        m, s = divmod(secs, 60)
        return f"{m}m {s}s" if s else f"{m}m"
    if secs < 86400:
        h, rem = divmod(secs, 3600)
        m = rem // 60
        return f"{h}h {m}m" if m else f"{h}h"
    d_, rem = divmod(secs, 86400)
    h = rem // 3600
    return f"{d_}d {h}h" if h else f"{d_}d"


# ── persistence ────────────────────────────────────────────────

def _load_reminders() -> dict:
    raw = load_json(REMINDERS_FILE) or {}
    return {
        "next_id": int(raw.get("next_id", 1)),
        "items": list(raw.get("items", [])),
    }


def _save_reminders(state: dict) -> None:
    save_json(REMINDERS_FILE, {
        "next_id": int(state.get("next_id", 1)),
        "items": list(state.get("items", [])),
    })


def _load_birthdays() -> dict:
    raw = load_json(BIRTHDAYS_FILE) or {}
    return {"items": list(raw.get("items", []))}


def _save_birthdays(state: dict) -> None:
    save_json(BIRTHDAYS_FILE, {"items": list(state.get("items", []))})


# ── target resolution for .bday ────────────────────────────────

_MENTION_RE = re.compile(r"<@!?(\d+)>")


def _resolve_target(bot: commands.Bot, raw: str | None, fallback: discord.User) -> tuple[int, str]:
    """Returns (target_id, target_name). `target_id` is 0 if it's a free-text
    label (so plain names work even without a resolvable user)."""
    if not raw:
        return fallback.id, fallback.display_name if hasattr(fallback, "display_name") else fallback.name
    raw = raw.strip()
    m = _MENTION_RE.match(raw)
    if m:
        uid = int(m.group(1))
        u = bot.get_user(uid)
        return uid, (u.display_name if (u and hasattr(u, "display_name")) else (u.name if u else f"User {uid}"))
    if raw.isdigit():
        uid = int(raw)
        u = bot.get_user(uid)
        return uid, (u.display_name if (u and hasattr(u, "display_name")) else (u.name if u else f"User {uid}"))
    # free-text label
    return 0, raw


# ────────────────────────────────────────────────────────────────

class RemindersCog(commands.Cog, name="Reminders"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_scan: dict[int, dict[int, str]] = {}  # guild_id → {1: "∞・", 2: "🔥"}
        self.task: Optional[asyncio.Task] = None

    async def cog_load(self):
        self.task = asyncio.create_task(self._scheduler())

    async def cog_unload(self):
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = None

    # ── scheduler ─────────────────────────────────────────────
    async def _scheduler(self):
        try:
            await self.bot.wait_until_ready()
        except Exception as e:
            log.warning("reminder scheduler wait_until_ready failed: %s", e)
            return
        try:
            INTERVAL = 30
            while not self.bot.is_closed():
                tick_start = _now_utc()
                try:
                    await self._tick()
                except Exception as e:
                    log.warning("reminder scheduler tick error: %s", e)
                elapsed = (_now_utc() - tick_start).total_seconds()
                await asyncio.sleep(max(0, INTERVAL - elapsed))
        except asyncio.CancelledError:
            pass

    async def _tick(self):
        now = _now_utc()
        # ── reminders ────
        state = _load_reminders()
        due, kept = [], []
        for it in state["items"]:
            try:
                trig = datetime.fromisoformat(it["trigger_iso"])
            except Exception:
                continue
            (due if trig <= now else kept).append(it)
        failed = []
        if due:
            for it in due:
                u = self.bot.get_user(it["user_id"])
                if u is None:
                    try:
                        u = await self.bot.fetch_user(it["user_id"])
                    except Exception:
                        failed.append(it)
                        continue
                try:
                    await u.send(
                        embed=discord.Embed(
                            title="⏰ reminder",
                            description=it["text"],
                            color=0xFF0000,
                        )
                    )
                except discord.HTTPException:
                    failed.append(it)
            state["items"] = kept + failed
            _save_reminders(state)

        # ── birthdays ────
        bs = _load_birthdays()
        today_md = now.strftime("%m-%d")
        tomorrow_md = (now + timedelta(days=1)).strftime("%m-%d")
        cur_year = now.year
        changed = False
        for it in bs["items"]:
            md = it.get("month_day")
            creator = None  # lazy-fetch only if we have something to send

            # ── day-before reminder ────
            if md == tomorrow_md and it.get("last_pre_notified_year") != cur_year:
                creator = self.bot.get_user(it["creator_id"])
                if creator is None:
                    try:
                        creator = await self.bot.fetch_user(it["creator_id"])
                    except Exception:
                        creator = None
                if creator is not None:
                    target_name = it.get("target_name", "someone")
                    try:
                        await creator.send(
                            embed=discord.Embed(
                                title="🎂 birthday tomorrow",
                                description=f"heads up — **{target_name}**'s birthday is tomorrow.",
                                color=0xFFB347,
                            )
                        )
                    except discord.HTTPException:
                        pass
                    it["last_pre_notified_year"] = cur_year
                    changed = True

            # ── day-of reminder ────
            if md == today_md and it.get("last_notified_year") != cur_year:
                if creator is None:
                    creator = self.bot.get_user(it["creator_id"])
                    if creator is None:
                        try:
                            creator = await self.bot.fetch_user(it["creator_id"])
                        except Exception:
                            creator = None
                if creator is not None:
                    target_name = it.get("target_name", "someone")
                    try:
                        await creator.send(
                            embed=discord.Embed(
                                title="🎂 birthday today!",
                                description=f"today is **{target_name}**'s birthday — go wish them.",
                                color=0xFF8800,
                            )
                        )
                    except discord.HTTPException:
                        pass
                    it["last_notified_year"] = cur_year
                    changed = True
        if changed:
            _save_birthdays(bs)

    # ── color helper that survives DMs ────────────────────────
    def _color(self, ctx) -> int:
        return get_embed_color(ctx.guild.id) if ctx.guild else 0x121516

    # ── .remind ────────────────────────────────────────────
    @help_meta(
        usage="`.remind <when> <text>` · `.remind list` · `.remind cancel <id>`",
        desc="DM-yourself reminder. `<when>` accepts `5s 5m 2h 2d 1w` or `YYYY/MM/DD [HH:MM]`.",
        section="Reminders",
    )
    @commands.group(name="remind", aliases=["reminder"], invoke_without_command=True)
    async def remind(self, ctx, when: str = None, *, text: str = None):
        if not when or not text:
            return await ctx.send(
                "-# usage: `.remind <when> <text>`\n"
                "-# • relative: `5s`, `5m`, `2h`, `2d`, `1w`\n"
                "-# • absolute: `2026/11/08` or `2026/11/08 14:30` (UTC)\n"
                "-# • `.remind list` · `.remind cancel <id>`"
            )
        trig = parse_when(when)
        if not trig:
            return await ctx.send("-# couldn't parse that time. try `5m`, `2h`, or `2026/11/08`")
        if trig <= _now_utc():
            return await ctx.send("-# that time is in the past")

        state = _load_reminders()
        rid = state["next_id"]
        state["next_id"] = rid + 1
        state["items"].append({
            "id": rid,
            "user_id": ctx.author.id,
            "trigger_iso": trig.isoformat(),
            "text": text.strip()[:500],
            "created_iso": _now_utc().isoformat(),
        })
        _save_reminders(state)
        delta = trig - _now_utc()
        await ctx.send(f"-# got u — DMing in **{_format_delta(delta)}** (`#{rid}`)")

    @help_meta(usage=".remind list", desc="list all your reminders.", section="Reminders")
    @remind.command(name="list")
    async def remind_list(self, ctx):
        state = _load_reminders()
        my = sorted(
            (it for it in state["items"] if it["user_id"] == ctx.author.id),
            key=lambda x: x["trigger_iso"],
        )
        if not my:
            return await ctx.send("-# no reminders set")
        lines = []
        now = _now_utc()
        for it in my:
            try:
                trig = datetime.fromisoformat(it["trigger_iso"])
                in_str = _format_delta(trig - now) if trig > now else "any sec"
            except Exception:
                in_str = "?"
            text_preview = it["text"][:80] + ("…" if len(it["text"]) > 80 else "")
            lines.append(f"`#{it['id']}` in **{in_str}** — {text_preview}")
        embed = discord.Embed(
            title="your reminders",
            description="\n".join(lines),
            color=self._color(ctx),
        )
        await ctx.send(embed=embed)

    @help_meta(usage=".remind cancel <id>", desc="cancel a reminder by its id.", section="Reminders")
    @remind.command(name="cancel", aliases=["rm", "del", "delete"])
    async def remind_cancel(self, ctx, rid: int):
        state = _load_reminders()
        before = len(state["items"])
        state["items"] = [
            it for it in state["items"]
            if not (it["id"] == rid and it["user_id"] == ctx.author.id)
        ]
        if len(state["items"]) == before:
            return await ctx.send(f"-# no reminder `#{rid}` of urs")
        _save_reminders(state)
        await ctx.send(f"-# cancelled `#{rid}`")

    # ── .bday ──────────────────────────────────────────────
    @help_meta(
        usage="`.bday <date> [@user|name]` · `.bday list` · `.bday remove [@user]`",
        desc="save a birthday — bot DMs you every year on the date.",
        section="Reminders",
    )
    @commands.group(name="bday", aliases=["birthday"], invoke_without_command=True)
    async def bday(self, ctx, date: str = None, *, target: str = None):
        if not date:
            return await ctx.send(
                "-# usage: `.bday <date> [@user or name]`\n"
                "-# • date: `2002/11/08` or just `11/08` (year optional)\n"
                "-# • `.bday list` · `.bday remove [@user]`"
            )
        md = parse_bday(date)
        if not md:
            return await ctx.send("-# couldn't parse the date. try `11/08` or `2002/11/08`")

        target_id, target_name = _resolve_target(self.bot, target, ctx.author)

        bs = _load_birthdays()
        # remove any existing entry for this (creator, target) so re-setting
        # overwrites instead of duplicating
        bs["items"] = [
            it for it in bs["items"]
            if not (it["creator_id"] == ctx.author.id
                    and it["target_id"] == target_id
                    and (target_id != 0 or it.get("target_name") == target_name))
        ]
        bs["items"].append({
            "creator_id": ctx.author.id,
            "target_id": target_id,
            "target_name": target_name,
            "month_day": md,
            "last_notified_year": 0,
            "last_pre_notified_year": 0,
        })
        _save_birthdays(bs)
        await ctx.send(
            f"-# saved — i'll DM u every year on `{md}` for **{target_name}**"
        )

    @help_meta(usage=".bday list", desc="list all your saved birthdays.", section="Reminders")
    @bday.command(name="list")
    async def bday_list(self, ctx):
        bs = _load_birthdays()
        my = sorted(
            (it for it in bs["items"] if it["creator_id"] == ctx.author.id),
            key=lambda x: x["month_day"],
        )
        if not my:
            return await ctx.send("-# no birthdays saved")
        lines = [f"`{it['month_day']}` — **{it['target_name']}**" for it in my]
        embed = discord.Embed(
            title="your saved birthdays",
            description="\n".join(lines),
            color=self._color(ctx),
        )
        await ctx.send(embed=embed)

    @help_meta(usage=".bday remove [@user]", desc="remove a saved birthday.", section="Reminders")
    @bday.command(name="remove", aliases=["rm", "del", "delete"])
    async def bday_remove(self, ctx, *, target: str = None):
        target_id, target_name = _resolve_target(self.bot, target, ctx.author)
        bs = _load_birthdays()
        before = len(bs["items"])
        bs["items"] = [
            it for it in bs["items"]
            if not (it["creator_id"] == ctx.author.id
                    and it["target_id"] == target_id
                    and (target_id != 0 or it.get("target_name") == target_name))
        ]
        if len(bs["items"]) == before:
            return await ctx.send(f"-# no birthday saved for **{target_name}**")
        _save_birthdays(bs)
        await ctx.send(f"-# removed birthday for **{target_name}**")


async def setup(bot: commands.Bot):
    await bot.add_cog(RemindersCog(bot))
