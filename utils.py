import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DATA_DIR = "data"
DB_FILE  = f"{DATA_DIR}/bot.db"

log = logging.getLogger(__name__)

# Command/channel allow/deny rules (guild only)
# Stored in SQLite via the existing kv+JSON layer, keyed by this "filepath".
CMD_CHANNEL_RULES_FILE = f"{DATA_DIR}/cmd_channel_rules.json"

# ── Legacy path constants (kept so every cog import still works) ─
CONFESSIONS_FILE          = f"{DATA_DIR}/confessions.json"
CONFIG_FILE               = f"{DATA_DIR}/config.json"
AUDIT_FILE                = f"{DATA_DIR}/audit.json"
CONVERSATIONS_FILE        = f"{DATA_DIR}/conversations.json"
CONVERSATIONS_BACKUP_FILE = f"{DATA_DIR}/conversations_backup.json"
BOT_MEMORY_FILE           = f"{DATA_DIR}/bot_memory.json"
BOT_MEMORY_BACKUP_FILE    = f"{DATA_DIR}/bot_memory_backup.json"
DM_WHITELIST_FILE         = f"{DATA_DIR}/dm_whitelist.json"
IGNORE_LIST_FILE          = f"{DATA_DIR}/ignore_list.json"
ALIASES_FILE              = f"{DATA_DIR}/aliases.json"

os.makedirs(DATA_DIR, exist_ok=True)

# ── SQLite setup (lazy — no side effects at import time) ─────────

# One connection per thread — sqlite3 connections aren't thread-safe.
_local = threading.local()
_db_initialized = False

def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads while writing
        conn.execute("PRAGMA synchronous=NORMAL")  # fast enough, still safe
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA cache_size=-64000")  # 64MB cache for better performance
        _local.conn = conn
    # Always ensure schema exists on a live connection.
    _ensure_db()
    return _local.conn

@contextmanager
def _db():
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise

def _ensure_db():
    global _db_initialized
    if _db_initialized:
        return
    conn = _local.conn if hasattr(_local, "conn") and _local.conn is not None else None
    if conn is None:
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kv (
            filepath TEXT NOT NULL,
            data     TEXT NOT NULL,
            PRIMARY KEY (filepath)
        )
    """)

    # NEW: Per-guild avatars table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_avatars (
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            avatar_url TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
    """)

    # NEW: Music playlists table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS playlists (
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            tracks TEXT NOT NULL,
            PRIMARY KEY (user_id, name)
        )
    """)

    # NEW: XP and leveling table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_xp (
            user_id TEXT NOT NULL,
            guild_id TEXT NOT NULL,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 0,
            messages INTEGER DEFAULT 0,
            voice_minutes INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, guild_id)
        )
    """)

    # NEW: Level roles table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS level_roles (
            guild_id TEXT NOT NULL,
            level INTEGER NOT NULL,
            role_id TEXT NOT NULL,
            PRIMARY KEY (guild_id, level)
        )
    """)

    _db_initialized = True

# ── List-file detection (same logic as before) ───────────────────

_LIST_FILES = frozenset({
    IGNORE_LIST_FILE,
    DM_WHITELIST_FILE,
    CONFESSIONS_FILE,
    AUDIT_FILE,
})

def _is_list_file(filepath: str) -> bool:
    return filepath in _LIST_FILES

# ── Core load / save (drop-in replacements) ──────────────────────

def load_json(filepath: str):
    """Load a JSON value from SQLite. Falls back to empty default on miss."""
    _run_migration_once()
    with _db() as conn:
        row = conn.execute(
            "SELECT data FROM kv WHERE filepath = ?", (filepath,)
        ).fetchone()
    if row is None:
        return [] if _is_list_file(filepath) else {}
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        log.warning("corrupt data for %s, returning empty default", filepath)
        return [] if _is_list_file(filepath) else {}


def save_json(filepath: str, data):
    """Upsert a JSON value into SQLite. Atomic — no tmp files needed."""
    _run_migration_once()
    try:
        serialized = json.dumps(data, ensure_ascii=False)
        with _db() as conn:
            conn.execute(
                "INSERT INTO kv (filepath, data) VALUES (?, ?) "
                "ON CONFLICT(filepath) DO UPDATE SET data = excluded.data",
                (filepath, serialized),
            )
    except Exception:
        log.exception("error saving %s", filepath)

# ── Migration helper: import existing JSON files on first run ─────

def migrate_json_files():
    """
    One-time migration: reads any existing .json files and imports them
    into SQLite, then renames them to .json.migrated so they're left as
    a backup but won't be re-imported on restart.
    """
    candidates = [
        CONFESSIONS_FILE, CONFIG_FILE, AUDIT_FILE,
        CONVERSATIONS_FILE, CONVERSATIONS_BACKUP_FILE,
        BOT_MEMORY_FILE, BOT_MEMORY_BACKUP_FILE,
        DM_WHITELIST_FILE, IGNORE_LIST_FILE, ALIASES_FILE,
    ]
    for fp in candidates:
        if not os.path.exists(fp):
            continue
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
            # Only import if SQLite doesn't already have this key
            with _db() as conn:
                existing = conn.execute(
                    "SELECT 1 FROM kv WHERE filepath = ?", (fp,)
                ).fetchone()
            if existing is None:
                save_json(fp, data)
                log.info("Migrated %s → SQLite", fp)
            # Rename so we don't import again
            os.rename(fp, fp + ".migrated")
            log.info("Renamed %s → %s.migrated (safe backup)", fp, fp)
        except Exception:
            log.warning("Migration warning for %s", fp, exc_info=True)

_migration_done = False

def _run_migration_once():
    global _migration_done
    if _migration_done:
        return
    migrate_json_files()
    _migration_done = True

# ── init_files: now just a no-op for compat ──────────────────────

def init_files():
    """Kept for import compatibility. SQLite schema is set up at module load."""
    pass

# ── Caches with TTL (Time-To-Live) for auto-refresh ─────────────────────────────────────

_CACHE_TTL_SECONDS = 300  # 5 minutes cache TTL

_config_cache       = None
_config_cache_time  = 0
_ignore_cache       = None
_ignore_cache_time  = 0
_dm_whitelist_cache = None
_dm_whitelist_cache_time = 0
_aliases_cache      = None
_aliases_cache_time = 0


def _cache_valid(cache_time: float) -> bool:
    """Check if cache is still valid based on TTL."""
    return (time.time() - cache_time) < _CACHE_TTL_SECONDS


def get_config():
    global _config_cache, _config_cache_time
    if _config_cache is None or not _cache_valid(_config_cache_time):
        _config_cache = load_json(CONFIG_FILE)
        _config_cache_time = time.time()
    return _config_cache


def invalidate_config():
    global _config_cache, _config_cache_time
    _config_cache = None
    _config_cache_time = 0


def get_ignore_list():
    global _ignore_cache, _ignore_cache_time
    if _ignore_cache is None or not _cache_valid(_ignore_cache_time):
        _ignore_cache = load_json(IGNORE_LIST_FILE)
        _ignore_cache_time = time.time()
    return _ignore_cache


def invalidate_ignore():
    global _ignore_cache, _ignore_cache_time
    _ignore_cache = None
    _ignore_cache_time = 0


def get_dm_whitelist():
    global _dm_whitelist_cache, _dm_whitelist_cache_time
    if _dm_whitelist_cache is None or not _cache_valid(_dm_whitelist_cache_time):
        _dm_whitelist_cache = load_json(DM_WHITELIST_FILE)
        _dm_whitelist_cache_time = time.time()
    return _dm_whitelist_cache


def invalidate_dm_whitelist():
    global _dm_whitelist_cache, _dm_whitelist_cache_time
    _dm_whitelist_cache = None
    _dm_whitelist_cache_time = 0


def get_aliases():
    global _aliases_cache, _aliases_cache_time
    if _aliases_cache is None or not _cache_valid(_aliases_cache_time):
        raw = load_json(ALIASES_FILE) or {}
        _aliases_cache = {
            str(k).lower(): str(v).lower() for k, v in raw.items()
        }
        _aliases_cache_time = time.time()
    return _aliases_cache


def invalidate_aliases():
    global _aliases_cache, _aliases_cache_time
    _aliases_cache = None
    _aliases_cache_time = 0

# ── Helpers ──────────────────────────────────────────────────────

def get_embed_color(guild_id):
    config = get_config()
    saved = config.get(str(guild_id), {}).get("embed_color")
    if saved is not None:
        return saved
    try:
        from neixoconfig import Neixocolor
        return Neixocolor
    except Exception:
        return 0xFF0000


def get_next_confession_id(guild_id):
    with _db() as conn:
        row = conn.execute(
            "SELECT data FROM kv WHERE filepath = ?", (CONFESSIONS_FILE,)
        ).fetchone()
        confessions = json.loads(row[0]) if row else {}
        max_id = 0
        for c in confessions.values():
            if c.get("guild_id") == str(guild_id):
                try:
                    cid = int(c.get("id", 0))
                except (TypeError, ValueError):
                    cid = 0
                if cid > max_id:
                    max_id = cid
        return max_id + 1


def get_next_reply_id():
    confessions = load_json(CONFESSIONS_FILE)
    reply_count = sum(len(c.get("replies", [])) for c in confessions.values())
    return reply_count + 1


_AUDIT_MAX_ENTRIES = 5000

def log_audit(action, guild_id, user_id, details):
    audits = load_json(AUDIT_FILE)
    audits.append({
        "action": action,
        "guild_id": str(guild_id),
        "user_id": str(user_id),
        "details": details,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    if len(audits) > _AUDIT_MAX_ENTRIES:
        audits = audits[-_AUDIT_MAX_ENTRIES:]
    save_json(AUDIT_FILE, audits)


# ── Date helper (used in ai.py system prompts) ───────────────────

def get_current_date_line() -> str:
    """Returns a compact current-date string to inject into AI system prompts."""
    now = datetime.now(timezone.utc)
    return now.strftime("current date and time: %A, %B %d, %Y, %H:%M UTC")


# ── GIF cooldown helpers (shared by misc & gif_editor cogs) ──────


import random as _random

GIF_COOLDOWN_SECONDS = 10
_gif_cooldowns: dict = {}
_gif_cooldowns_lock = threading.Lock()

def check_gif_cooldown(user_id: int):
    with _gif_cooldowns_lock:
        now = datetime.now(timezone.utc)
        for k in [k for k, v in _gif_cooldowns.items() if isinstance(v, datetime) and v < now]:
            _gif_cooldowns.pop(k, None)
            _gif_cooldowns.pop(f"{k}_warned", None)
        if user_id in _gif_cooldowns:
            time_left = (_gif_cooldowns[user_id] - now).total_seconds()
            if time_left > 0:
                if _gif_cooldowns.get(f"{user_id}_warned"):
                    return "silent"
                _gif_cooldowns[f"{user_id}_warned"] = True
                return time_left
        _gif_cooldowns[user_id] = now + timedelta(seconds=GIF_COOLDOWN_SECONDS)
        _gif_cooldowns.pop(f"{user_id}_warned", None)
        return None

def gif_cooldown_msg(seconds):
    msgs = [
        f"chill lol, wait {seconds}s",
        f"ur too fast omg.. {seconds}s",
        f"slow down bestie {seconds}s",
        f"nuh uh {seconds}s",
        f"wait ur turn {seconds}s",
        f"patience... {seconds}s",
        f"g-go easy on me {seconds}s",
        f"CHILLLL {seconds}s",
        f"sloW DOWN {seconds}s",
        f"nuh uh slower {seconds}s",
    ]
    return _random.choice(msgs)

# ── Imagine cooldown ──────────────────────────────────────────
IMAGINE_COOLDOWN_SECONDS = 25
_imagine_cooldowns: dict = {}
_imagine_cooldowns_lock = threading.Lock()

def check_imagine_cooldown(user_id: int):
    with _imagine_cooldowns_lock:
        now = datetime.now(timezone.utc)
        for k in [k for k, v in _imagine_cooldowns.items() if isinstance(v, datetime) and v < now]:
            _imagine_cooldowns.pop(k, None)
            _imagine_cooldowns.pop(f"{k}_warned", None)
        if user_id in _imagine_cooldowns:
            time_left = (_imagine_cooldowns[user_id] - now).total_seconds()
            if time_left > 0:
                if _imagine_cooldowns.get(f"{user_id}_warned"):
                    return "silent"
                _imagine_cooldowns[f"{user_id}_warned"] = True
                return time_left
        _imagine_cooldowns[user_id] = now + timedelta(seconds=IMAGINE_COOLDOWN_SECONDS)
        _imagine_cooldowns.pop(f"{user_id}_warned", None)
        return None

def imagine_cooldown_msg(seconds):
    msgs = [
        f"chill lol, wait {seconds}s",
        f"ur too fast omg.. {seconds}s",
        f"slow down bestie {seconds}s",
        f"nuh uh {seconds}s",
        f"wait ur turn {seconds}s",
        f"patience... {seconds}s",
        f"g-go easy on me {seconds}s",
        f"CHILLLL {seconds}s",
        f"sloW DOWN {seconds}s",
        f"nuh uh slower {seconds}s",
    ]
    return _random.choice(msgs)

# ── Constants ────────────────────────────────────────────────────

from neixoconfig import SeoulitiesServerID as SEOULITIES_SERVER_ID

CREATOR_ID           = int(os.getenv("CREATOR_ID", "0"))


def is_creator(user_id) -> bool:
    try:
        return int(user_id) == CREATOR_ID
    except (TypeError, ValueError):
        return False


def is_owner_or_creator(ctx) -> bool:
    if ctx.author.id == CREATOR_ID:
        return True
    if ctx.guild is not None and ctx.author.id == ctx.guild.owner_id:
        return True
    return False

# ── Command/channel allow/deny rules (guild only) ──────────────────────
#
# File: data/cmd_channel_rules.json
#
# Structure:
# {
#   "<guild_id>": {
#      "targets": {
#         "<target_key>": {               # target_key is a category id or a command meta key
#            "mode": "allow" | "deny",
#            "channels": ["<channel_id_str>", ...]
#         }
#      }
#   }
# }
#
# target_key examples:
#   - category id from COG_META["category"] (e.g. "music", "theme")
#   - or a specific cog command key from COG_META["commands"] (e.g. "play", "theme apply")
#
# If a target_key does not exist, the command works in all channels.
#
def get_cmd_channel_rules() -> dict:
    return load_json(CMD_CHANNEL_RULES_FILE) or {}

def get_guild_cmd_rule_targets(guild_id: int | str) -> dict:
    rules = get_cmd_channel_rules()
    g = rules.get(str(guild_id), {})
    if isinstance(g, dict):
        return g.get("targets", {}) or {}
    return {}

def set_cmd_channel_rule(guild_id: int | str, target_key: str, mode: str, channel_ids: list[int | str]) -> None:
    mode = (mode or "").lower().strip()
    if mode not in ("allow", "deny"):
        raise ValueError("mode must be 'allow' or 'deny'")

    rules = get_cmd_channel_rules()
    gid = str(guild_id)

    rules.setdefault(gid, {})
    rules[gid].setdefault("targets", {})
    rules[gid]["targets"][target_key] = {
        "mode": mode,
        "channels": [str(c) for c in channel_ids],
    }
    save_json(CMD_CHANNEL_RULES_FILE, rules)

def clear_cmd_channel_rule(guild_id: int | str, target_key: str) -> None:
    rules = get_cmd_channel_rules()
    gid = str(guild_id)
    targets = rules.get(gid, {}).get("targets", {})
    if target_key in targets:
        del targets[target_key]
    if gid in rules:
        rules[gid]["targets"] = targets
    save_json(CMD_CHANNEL_RULES_FILE, rules)

def clear_cmd_channel_rules(guild_id: int | str) -> None:
    rules = get_cmd_channel_rules()
    gid = str(guild_id)
    if gid in rules:
        rules[gid]["targets"] = {}
    save_json(CMD_CHANNEL_RULES_FILE, rules)

def get_cmd_channel_rule(guild_id: int | str, target_key: str) -> dict | None:
    return get_guild_cmd_rule_targets(guild_id).get(target_key)

def help_meta(
    *,
    section: str = None,
    usage: str = None,
    desc: str = None,
    staff: bool = False,
    owner: bool = False,
    admin: bool = False,
    examples: list[str] | None = None,
    params: list[dict] | None = None,
    note: str | None = None,
):
    """Decorator to attach help metadata to a command."""
    def decorator(func):
        meta = {
            "section": section,
            "usage": usage,
            "desc": desc,
            "staff": staff,
            "owner": owner,
            "admin": admin,
            "examples": examples or [],
            "params": params or [],
            "note": note,
        }
        func.__dict__["help_meta"] = meta
        callback = getattr(func, "callback", None)
        if callback is not None:
            callback.__dict__["help_meta"] = meta
        return func
    return decorator

def get_help_meta(cmd) -> dict | None:
    meta = getattr(cmd, "help_meta", None)
    if not meta:
        callback = getattr(cmd, "callback", None)
        meta = getattr(callback, "help_meta", None) if callback else None
    return meta


# ── NEW: Per-Guild Avatar Helpers ──────────────────────────────────────

def set_guild_avatar(guild_id: int | str, user_id: int | str, avatar_url: str) -> None:
    """Set a custom avatar for a user in a specific guild."""
    with _db() as conn:
        conn.execute(
            "INSERT INTO guild_avatars (guild_id, user_id, avatar_url) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET avatar_url = excluded.avatar_url",
            (str(guild_id), str(user_id), avatar_url)
        )


def get_guild_avatar(guild_id: int | str, user_id: int | str) -> str | None:
    """Get custom avatar for a user in a specific guild. Returns None if not set."""
    with _db() as conn:
        row = conn.execute(
            "SELECT avatar_url FROM guild_avatars WHERE guild_id = ? AND user_id = ?",
            (str(guild_id), str(user_id))
        ).fetchone()
    return row[0] if row else None


def remove_guild_avatar(guild_id: int | str, user_id: int | str) -> bool:
    """Remove custom avatar for a user in a specific guild. Returns True if removed."""
    with _db() as conn:
        cursor = conn.execute(
            "DELETE FROM guild_avatars WHERE guild_id = ? AND user_id = ?",
            (str(guild_id), str(user_id))
        )
        return cursor.rowcount > 0


# ── NEW: Music Playlist Helpers ──────────────────────────────────────

def save_playlist(user_id: int | str, name: str, tracks: list) -> None:
    """Save a playlist for a user."""
    with _db() as conn:
        conn.execute(
            "INSERT INTO playlists (user_id, name, tracks) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, name) DO UPDATE SET tracks = excluded.tracks",
            (str(user_id), name.lower(), json.dumps(tracks))
        )


def load_playlist(user_id: int | str, name: str) -> list | None:
    """Load a playlist by name. Returns None if not found."""
    with _db() as conn:
        row = conn.execute(
            "SELECT tracks FROM playlists WHERE user_id = ? AND name = ?",
            (str(user_id), name.lower())
        ).fetchone()
    return json.loads(row[0]) if row else None


def delete_playlist(user_id: int | str, name: str) -> bool:
    """Delete a playlist. Returns True if deleted."""
    with _db() as conn:
        cursor = conn.execute(
            "DELETE FROM playlists WHERE user_id = ? AND name = ?",
            (str(user_id), name.lower())
        )
        return cursor.rowcount > 0


def list_playlists(user_id: int | str) -> list[str]:
    """List all playlist names for a user."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT name FROM playlists WHERE user_id = ?",
            (str(user_id),)
        ).fetchall()
    return [row[0] for row in rows]


# ── NEW: XP & Leveling Helpers ──────────────────────────────────────

def add_xp(user_id: int | str, guild_id: int | str, xp_amount: int = 10, messages: int = 1, voice_minutes: int = 0) -> dict:
    """
    Add XP to a user. Returns dict with level info if leveled up.
    Default: 10 XP per message.
    """
    if xp_amount < 0:
        xp_amount = 0

    level_up_info = {"leveled_up": False}

    with _db() as conn:
        row = conn.execute(
            "SELECT xp, level, messages, voice_minutes FROM user_xp WHERE user_id = ? AND guild_id = ?",
            (str(user_id), str(guild_id))
        ).fetchone()

        if row:
            current_xp, current_level, current_messages, current_voice = row
            new_xp = current_xp + xp_amount
            new_messages = current_messages + messages
            new_voice = current_voice + voice_minutes
        else:
            new_xp = xp_amount
            current_level = 0
            new_messages = messages
            new_voice = voice_minutes

        import math
        new_level = int(math.sqrt(new_xp / 100))

        if new_level > current_level:
            level_up_info["leveled_up"] = True
            level_up_info["old_level"] = current_level
            level_up_info["new_level"] = new_level

        conn.execute(
            "INSERT INTO user_xp (user_id, guild_id, xp, level, messages, voice_minutes) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, guild_id) DO UPDATE SET "
            "xp = excluded.xp, level = excluded.level, messages = excluded.messages, voice_minutes = excluded.voice_minutes",
            (str(user_id), str(guild_id), new_xp, new_level, new_messages, new_voice)
        )

        level_up_info["xp"] = new_xp
        level_up_info["level"] = new_level
        level_up_info["messages"] = new_messages
        level_up_info["voice_minutes"] = new_voice

    return level_up_info


def add_voice_xp(user_id: int | str, guild_id: int | str, minutes: int = 1) -> dict:
    """Add voice time XP (5 XP per minute). Returns level info if leveled up."""
    return add_xp(user_id, guild_id, xp_amount=minutes * 5, messages=0, voice_minutes=minutes)


def get_user_xp(user_id: int | str, guild_id: int | str) -> dict | None:
    """Get XP data for a user in a guild."""
    with _db() as conn:
        row = conn.execute(
            "SELECT xp, level, messages, voice_minutes FROM user_xp WHERE user_id = ? AND guild_id = ?",
            (str(user_id), str(guild_id))
        ).fetchone()
    if row:
        return {"xp": row[0], "level": row[1], "messages": row[2], "voice_minutes": row[3]}
    return None


def get_leaderboard(guild_id: int | str, limit: int = 10) -> list[dict]:
    """Get top users by XP in a guild."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT user_id, xp, level, messages FROM user_xp "
            "WHERE guild_id = ? ORDER BY xp DESC LIMIT ?",
            (str(guild_id), limit)
        ).fetchall()
    return [{"user_id": row[0], "xp": row[1], "level": row[2], "messages": row[3]} for row in rows]


# ── NEW: Level Roles Helpers ──────────────────────────────────────

def set_level_role(guild_id: int | str, level: int, role_id: int | str) -> None:
    """Set a role to be given at a specific level."""
    with _db() as conn:
        conn.execute(
            "INSERT INTO level_roles (guild_id, level, role_id) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, level) DO UPDATE SET role_id = excluded.role_id",
            (str(guild_id), level, str(role_id))
        )


def get_level_role(guild_id: int | str, level: int) -> str | None:
    """Get role ID for a specific level."""
    with _db() as conn:
        row = conn.execute(
            "SELECT role_id FROM level_roles WHERE guild_id = ? AND level = ?",
            (str(guild_id), level)
        ).fetchone()
    return row[0] if row else None


def get_all_level_roles(guild_id: int | str) -> dict[int, str]:
    """Get all level-role mappings for a guild."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT level, role_id FROM level_roles WHERE guild_id = ?",
            (str(guild_id),)
        ).fetchall()
    return {row[0]: row[1] for row in rows}

