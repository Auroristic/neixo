"""
theme_manager.py  —  NeixO Theme System
Handles all data access, unicode font conversion, and theme logic.
Uses a dedicated theme.db (separate from bot.db) via the same kv pattern.
"""

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager

log = logging.getLogger(__name__)

DATA_DIR   = "data"
THEME_DB   = f"{DATA_DIR}/theme.db"

os.makedirs(DATA_DIR, exist_ok=True)

# ── DB bootstrap ─────────────────────────────────────────────────

_local = threading.local()

def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(THEME_DB, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
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


def _init_db():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS theme_kv (
                scope TEXT NOT NULL,
                key   TEXT NOT NULL,
                data  TEXT NOT NULL,
                PRIMARY KEY (scope, key)
            )
        """)

_init_db()

# ── Core kv helpers ───────────────────────────────────────────────

def _load(scope: str, key: str):
    conn = _get_conn()
    row = conn.execute(
        "SELECT data FROM theme_kv WHERE scope=? AND key=?", (scope, key)
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None

def _save(scope: str, key: str, data) -> None:
    serialized = json.dumps(data, ensure_ascii=False)
    with _db() as conn:
        conn.execute(
            "INSERT INTO theme_kv(scope,key,data) VALUES(?,?,?) "
            "ON CONFLICT(scope,key) DO UPDATE SET data=excluded.data",
            (scope, key, serialized),
        )

def _delete(scope: str, key: str) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM theme_kv WHERE scope=? AND key=?", (scope, key))

def _list_keys(scope: str) -> list[str]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT key FROM theme_kv WHERE scope=?", (scope,)
    ).fetchall()
    return [r[0] for r in rows]

# ── Scope keys ────────────────────────────────────────────────────
# scope="guild:{guild_id}"  key="role_map"    → {slot_name: role_id, ...}
# scope="guild:{guild_id}"  key="theme"       → current theme dict
# scope="guild:{guild_id}"  key="snapshot"    → pre-apply snapshot
# scope="presets"           key="{name}"      → preset theme dict

def _guild_scope(guild_id) -> str:
    return f"guild:{guild_id}"

# ── Role map (slot_name → role_id) ────────────────────────────────

def get_role_map(guild_id: int) -> dict:
    """Returns {slot_name: role_id_int, ...} or {}."""
    return _load(_guild_scope(guild_id), "role_map") or {}

def save_role_map(guild_id: int, role_map: dict) -> None:
    _save(_guild_scope(guild_id), "role_map", role_map)

def add_role_slot(guild_id: int, slot_name: str, role_id: int) -> None:
    rm = get_role_map(guild_id)
    rm[slot_name] = role_id
    save_role_map(guild_id, rm)

def remove_role_slot(guild_id: int, slot_name: str) -> bool:
    rm = get_role_map(guild_id)
    if slot_name not in rm:
        return False
    del rm[slot_name]
    save_role_map(guild_id, rm)
    return True

# ── Theme (current saved theme for a guild) ───────────────────────

def get_guild_theme(guild_id: int) -> dict | None:
    return _load(_guild_scope(guild_id), "theme")

def save_guild_theme(guild_id: int, theme: dict) -> None:
    _save(_guild_scope(guild_id), "theme", theme)

def clear_guild_theme(guild_id: int) -> None:
    _delete(_guild_scope(guild_id), "theme")

# ── Snapshot (undo) ───────────────────────────────────────────────

def push_undo_snapshot(guild_id: int, snapshot: dict) -> None:
    """Push a snapshot onto the per-guild undo stack (max 5 entries).

    snapshot shape:
      {"roles": {role_id: {"name": str, ...}}, "channels": {ch_id: {"name": str}}}
    """
    stack = _load(_guild_scope(guild_id), "undo_stack") or []
    stack.append(snapshot)
    # keep a short bounded history
    if len(stack) > 5:
        stack = stack[-5:]
    _save(_guild_scope(guild_id), "undo_stack", stack)


def pop_undo_snapshot(guild_id: int) -> dict | None:
    """Pop and return the most recent snapshot from the undo stack."""
    stack = _load(_guild_scope(guild_id), "undo_stack") or []
    if not stack:
        return None
    last = stack.pop()
    _save(_guild_scope(guild_id), "undo_stack", stack)
    return last


def get_undo_stack(guild_id: int) -> list:
    return _load(_guild_scope(guild_id), "undo_stack") or []


def clear_undo_stack(guild_id: int) -> None:
    _delete(_guild_scope(guild_id), "undo_stack")


def save_factory_snapshot(guild_id: int, snapshot: dict) -> None:
    """Save an immutable factory snapshot taken after first setup."""
    existing = _load(_guild_scope(guild_id), "factory_snapshot")
    if not existing:
        _save(_guild_scope(guild_id), "factory_snapshot", snapshot)


def get_factory_snapshot(guild_id: int) -> dict | None:
    return _load(_guild_scope(guild_id), "factory_snapshot")


# Backwards-compatible wrappers
def save_snapshot(guild_id: int, snapshot: dict) -> None:
    push_undo_snapshot(guild_id, snapshot)


def get_snapshot(guild_id: int) -> dict | None:
    stack = get_undo_stack(guild_id)
    if not stack:
        return None
    return stack[-1]


def clear_snapshot(guild_id: int) -> None:
    clear_undo_stack(guild_id)

# ── Per-guild theme definition ────────────────────────────────────
#
# Theme dict structure:
# {
#   "name": str,
#   "roles": {
#     slot_name: {"name": str, "icon_url": str|None}
#   },
#   "channel_prefix": {
#     category_id_str: str   # emoji/prefix to put before channel names
#   },
#   "channel_style": {
#     "font": str,           # key from UNICODE_FONTS
#     "scope": "all" | [category_id_str, ...]
#   }
# }

def build_empty_theme(name: str = "untitled") -> dict:
    return {
        "name": name,
        "roles": {},
        "channel_prefix": {},
        "channel_style": {},
    }

# ── Presets ───────────────────────────────────────────────────────

def list_presets() -> list[str]:
    return _list_keys("presets")

def get_preset(name: str) -> dict | None:
    return _load("presets", name.lower())

def save_preset(name: str, theme: dict) -> None:
    _save("presets", name.lower(), theme)

def delete_preset(name: str) -> bool:
    if get_preset(name) is None:
        return False
    _delete("presets", name.lower())
    return True

# ── Unicode font conversion ───────────────────────────────────────

# Mapping tables: normal a-z A-Z 0-9 → styled codepoints
# We only map printable ASCII; non-ASCII chars pass through unchanged.

def _make_table(lowers: str, uppers: str, digits: str = "") -> dict:
    t: dict[int, str] = {}
    base_l = list("abcdefghijklmnopqrstuvwxyz")
    base_u = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    base_d = list("0123456789")
    for i, ch in enumerate(lowers):
        if ch:
            t[ord(base_l[i])] = ch
    for i, ch in enumerate(uppers):
        if ch:
            t[ord(base_u[i])] = ch
    if digits:
        for i, ch in enumerate(digits):
            if ch:
                t[ord(base_d[i])] = ch
    return t

# ── font tables ───────────────────────────────────────────────────

_BOLD_LOWER   = "𝐚𝐛𝐜𝐝𝐞𝐟𝐠𝐡𝐢𝐣𝐤𝐥𝐦𝐧𝐨𝐩𝐪𝐫𝐬𝐭𝐮𝐯𝐰𝐱𝐲𝐳"
_BOLD_UPPER   = "𝐀𝐁𝐂𝐃𝐄𝐅𝐆𝐇𝐈𝐉𝐊𝐋𝐌𝐍𝐎𝐏𝐐𝐑𝐒𝐓𝐔𝐕𝐖𝐗𝐘𝐙"
_BOLD_DIGITS  = "𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗"

_ITALIC_LOWER  = "𝘢𝘣𝘤𝘥𝘦𝘧𝘨𝘩𝘪𝘫𝘬𝘭𝘮𝘯𝘰𝘱𝘲𝘳𝘴𝘵𝘶𝘷𝘸𝘹𝘺𝘻"
_ITALIC_UPPER  = "𝘈𝘉𝘊𝘋𝘌𝘍𝘎𝘏𝘐𝘑𝘒𝘓𝘔𝘕𝘖𝘗𝘘𝘙𝘚𝘛𝘜𝘝𝘞𝘟𝘠𝘡"

_SCRIPT_LOWER  = "𝒶𝒷𝒸𝒹𝑒𝒻𝑔𝒽𝒾𝒿𝓀𝓁𝓂𝓃𝑜𝓅𝓆𝓇𝓈𝓉𝓊𝓋𝓌𝓍𝓎𝓏"
_SCRIPT_UPPER  = "𝒜𝐵𝒞𝒟𝐸𝐹𝒢𝐻𝐼𝒥𝒦𝐿𝑀𝒩𝒪𝒫𝒬𝑅𝒮𝒯𝒰𝒱𝒲𝒳𝒴𝒵"

_DOUBLE_LOWER  = "𝕒𝕓𝕔𝕕𝕖𝕗𝕘𝕙𝕚𝕛𝕜𝕝𝕞𝕟𝕠𝕡𝕢𝕣𝕤𝕥𝕦𝕧𝕨𝕩𝕪𝕫"
_DOUBLE_UPPER  = "𝔸𝔹ℂ𝔻𝔼𝔽𝔾ℍ𝕀𝕁𝕂𝕃𝕄ℕ𝕆ℙℚℝ𝕊𝕋𝕌𝕍𝕎𝕏𝕐ℤ"
_DOUBLE_DIGITS = "𝟘𝟙𝟚𝟛𝟜𝟝𝟞𝟟𝟠𝟡"

_SMALLCAPS_LOWER = "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘQʀꜱᴛᴜᴠᴡxʏᴢ"
_SMALLCAPS_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_SANS_LOWER   = "𝖺𝖻𝖼𝖽𝖾𝖿𝗀𝗁𝗂𝗃𝗄𝗅𝗆𝗇𝗈𝗉𝗊𝗋𝗌𝗍𝗎𝗏𝗐𝗑𝗒𝗓"
_SANS_UPPER   = "𝖠𝖡𝖢𝖣𝖤𝖥𝖦𝖧𝖨𝖩𝖪𝖫𝖬𝖭𝖮𝖯𝖰𝖱𝖲𝖳𝖴𝖵𝖶𝖷𝖸𝖹"

_FRAKTUR_LOWER = "𝔞𝔟𝔠𝔡𝔢𝔣𝔤𝔥𝔦𝔧𝔨𝔩𝔪𝔫𝔬𝔭𝔮𝔯𝔰𝔱𝔲𝔳𝔴𝔵𝔶𝔷"
_FRAKTUR_UPPER = "𝔄𝔅ℭ𝔇𝔈𝔉𝔊ℌℑ𝔍𝔎𝔏𝔐𝔑𝔒𝔓𝔔ℜ𝔖𝔗𝔘𝔙𝔚𝔛𝔜ℨ"

_MONO_LOWER   = "𝚊𝚋𝚌𝚍𝚎𝚏𝚐𝚑𝚒𝚓𝚔𝚕𝚖𝚗𝚘𝚙𝚚𝚛𝚜𝚝𝚞𝚟𝚠𝚡𝚢𝚣"
_MONO_UPPER   = "𝙰𝙱𝙲𝙳𝙴𝙵𝙶𝙷𝙸𝙹𝙺𝙻𝙼𝙽𝙾𝙿𝚀𝚁𝚂𝚃𝚄𝚅𝚆𝚇𝚈𝚉"
_MONO_DIGITS  = "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿"

# Registry: key → (table, display_name, example)
UNICODE_FONTS: dict[str, dict] = {
    "bold": {
        "table": _make_table(_BOLD_LOWER, _BOLD_UPPER, _BOLD_DIGITS),
        "label": "Bold",
        "example": "𝐠𝐞𝐧𝐞𝐫𝐚𝐥",
    },
    "italic": {
        "table": _make_table(_ITALIC_LOWER, _ITALIC_UPPER),
        "label": "Italic",
        "example": "𝘨𝘦𝘯𝘦𝘳𝘢𝘭",
    },
    "script": {
        "table": _make_table(_SCRIPT_LOWER, _SCRIPT_UPPER),
        "label": "Script / Cursive",
        "example": "𝑔𝑒𝓃𝑒𝓇𝒶𝓁",
    },
    "double": {
        "table": _make_table(_DOUBLE_LOWER, _DOUBLE_UPPER, _DOUBLE_DIGITS),
        "label": "Double-struck",
        "example": "𝕘𝕖𝕟𝕖𝕣𝕒𝕝",
    },
    "smallcaps": {
        "table": _make_table(_SMALLCAPS_LOWER, _SMALLCAPS_UPPER),
        "label": "Small Caps",
        "example": "ɢᴇɴᴇʀᴀʟ",
    },
    "sans": {
        "table": _make_table(_SANS_LOWER, _SANS_UPPER),
        "label": "Sans-serif",
        "example": "𝗀𝖾𝗇𝖾𝗋𝖺𝗅",
    },
    "fraktur": {
        "table": _make_table(_FRAKTUR_LOWER, _FRAKTUR_UPPER),
        "label": "Fraktur / Gothic",
        "example": "𝔤𝔢𝔫𝔢𝔯𝔞𝔩",
    },
    "mono": {
        "table": _make_table(_MONO_LOWER, _MONO_UPPER, _MONO_DIGITS),
        "label": "Monospace",
        "example": "𝚐𝚎𝚗𝚎𝚛𝚊𝚕",
    },
}

def convert_font(text: str, font_key: str) -> str:
    """Convert ASCII characters in text to the given unicode font style."""
    info = UNICODE_FONTS.get(font_key)
    if not info:
        return text
    table = info["table"]
    return text.translate(table)

def _build_reverse_font_map() -> dict[str, str]:
    rev: dict[str, str] = {}
    for info in UNICODE_FONTS.values():
        for src_cp, styled in info["table"].items():
            for ch in styled:
                rev[ch] = chr(src_cp)
    return rev

_REVERSE_FONT_MAP: dict[str, str] = _build_reverse_font_map()

def strip_font(text: str) -> str:
    """Best-effort: map styled unicode chars back to plain ASCII."""
    return "".join(_REVERSE_FONT_MAP.get(c, c) for c in text)

# ── Channel prefix helpers ────────────────────────────────────────

def detect_prefix(channel_name: str) -> str | None:
    """
    Detect a leading unicode symbol/emoji prefix in a channel name.
    Prefix = any leading non-ascii chars before the first ascii letter/digit/hyphen.
    No separator required — supports both 🔥chat and 🔥-chat formats.
    Returns the prefix string or None.
    """
    import unicodedata
    result = []
    for ch in channel_name:
        # ascii letter, digit, or hyphen = start of real name
        if ch == "-" or (ch.isascii() and (ch.isalpha() or ch.isdigit())):
            break
        # reject plain ascii chars that aren't emoji/unicode symbols
        if ch.isascii():
            break
        result.append(ch)

    if not result:
        return None

    prefix = "".join(result)
    # must have something after the prefix
    rest = channel_name[len(prefix):]
    if not rest:
        return None
    return prefix

def apply_prefix(channel_name: str, prefix: str) -> str:
    """Replace any existing prefix on channel_name with new prefix."""
    existing = detect_prefix(channel_name)
    if existing:
        channel_name = channel_name[len(existing):].lstrip("-")
    return f"{prefix}{channel_name}" if prefix else channel_name

def remove_prefix_from_name(channel_name: str) -> str:
    """Strip any detected prefix (and optional trailing hyphen) from channel_name."""
    existing = detect_prefix(channel_name)
    if existing:
        channel_name = channel_name[len(existing):]
        # strip optional hyphen separator if present
        if channel_name.startswith("-"):
            channel_name = channel_name[1:]
    return channel_name

# ── Prefix history (one-level undo for prefix operations) ─────────

def save_prefix_history(guild_id: int, entry: dict) -> None:
    """
    entry = {
        "op": str,                          # e.g. "prefix_all", "prefix_add", "prefix_remove"
        "channels": {ch_id_str: old_name},  # channel id → name BEFORE the operation
    }
    """
    _save(_guild_scope(guild_id), "prefix_history", entry)

def get_prefix_history(guild_id: int) -> dict | None:
    return _load(_guild_scope(guild_id), "prefix_history")

def clear_prefix_history(guild_id: int) -> None:
    _delete(_guild_scope(guild_id), "prefix_history")

# Persist last scan (so `.theme prefix replace` survives restarts)
def save_last_scan(guild_id: int, data) -> None:
    """Store numbered scan results under key 'last_scan' for a guild."""
    _save(_guild_scope(guild_id), "last_scan", data)


def get_last_scan(guild_id: int) -> dict | None:
    return _load(_guild_scope(guild_id), "last_scan")


# ── Prefix groups ─────────────────────────────────────────────────
#
# groups = {
#   "main":  {"prefix": "∞・", "categories": [cat_id_str, ...]},
#   "media": {"prefix": "🔥",  "categories": [cat_id_str, ...]},
# }

def get_prefix_groups(guild_id: int) -> dict:
    return _load(_guild_scope(guild_id), "prefix_groups") or {}

def save_prefix_groups(guild_id: int, groups: dict) -> None:
    _save(_guild_scope(guild_id), "prefix_groups", groups)

def clear_prefix_groups(guild_id: int) -> None:
    _delete(_guild_scope(guild_id), "prefix_groups")

def get_group_for_category(guild_id: int, cat_id: int) -> tuple[str, str] | None:
    """Returns (group_name, prefix) if the category belongs to a group, else None."""
    groups = get_prefix_groups(guild_id)
    cat_id_str = str(cat_id)
    for name, data in groups.items():
        if cat_id_str in data.get("categories", []):
            return (name, data["prefix"])
    return None

# ── Plain-word detection for prefix safety ────────────────────────

def is_plain_word(text: str) -> bool:
    """Returns True if text looks like a plain ASCII word rather than an emoji/symbol."""
    import re
    return bool(re.match(r'^[A-Za-z]+$', text.strip()))
