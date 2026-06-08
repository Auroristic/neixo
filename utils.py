import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
from functools import lru_cache
import time

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
    _db_initialized = True

# ── List-file detection (same logic as before) ───────────────────

def _is_list_file(filepath: str) -> bool:
    return 'list' in os.path.basename(filepath)

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
            with open(fp, "r", encoding="utf-8") as f:
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
    confessions = load_json(CONFESSIONS_FILE)
    guild_confessions = [
        c for c in confessions.values()
        if c.get("guild_id") == str(guild_id)
    ]
    return len(guild_confessions) + 1


def get_next_reply_id():
    confessions = load_json(CONFESSIONS_FILE)
    reply_count = sum(len(c.get("replies", [])) for c in confessions.values())
    return reply_count + 1


def log_audit(action, guild_id, user_id, details):
    audits = load_json(AUDIT_FILE)
    audits.append({
        "action":    action,
        "guild_id":  str(guild_id),
        "user_id":   str(user_id),
        "details":   details,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    save_json(AUDIT_FILE, audits)


# ── Date helper (used in ai.py system prompts) ───────────────────

def get_current_date_line() -> str:
    """Returns a compact current-date string to inject into AI system prompts."""
    now = datetime.now(timezone.utc)
    return now.strftime("current date and time: %A, %B %d, %Y, %H:%M UTC")


# ── GIF cooldown helpers (shared by misc & gif_editor cogs) ──────

from datetime import datetime as _dt, timedelta, timezone
import random as _random

GIF_COOLDOWN_SECONDS = 10
_gif_cooldowns: dict = {}

def check_gif_cooldown(user_id: int):
    now = _dt.now(timezone.utc)
    for k in [k for k, v in _gif_cooldowns.items() if isinstance(v, _dt) and v < now]:
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

# ── Constants ────────────────────────────────────────────────────

from neixoconfig import SeoulitiesServerID as SEOULITIES_SERVER_ID
CREATOR_ID           = int(os.getenv("CREATOR_ID", "887382911924441139"))


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
):
    """Decorator to attach help metadata to a command."""
    def decorator(func):
        meta = {
            "section": section,
            "usage": usage,
            "desc": desc,
            "staff": staff,
            "owner": owner,
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

