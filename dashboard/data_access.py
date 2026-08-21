# dashboard/data_access.py
from cogs.giveaways import GIVEAWAYS_FILE
from cogs.reminders import REMINDERS_FILE

from utils import _db, load_json


def leaderboard(limit: int = 50) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT user_id, guild_id, xp, level, messages FROM user_xp "
            "ORDER BY xp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        dict(zip(("user_id", "guild_id", "xp", "level", "messages"), r)) for r in rows
    ]


def set_xp(user_id: str, guild_id: str, xp: int, level: int) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "UPDATE user_xp SET xp = ?, level = ? WHERE user_id = ? AND guild_id = ?",
            (int(xp), int(level), str(user_id), str(guild_id)),
        )
        return cur.rowcount > 0


def giveaways_view() -> list[dict]:
    state = load_json(GIVEAWAYS_FILE) or {}
    out = []
    for gid, gs in state.items():
        for mid, g in (gs or {}).items():
            out.append(
                {
                    "guild_id": gid,
                    "message_id": mid,
                    "prize": (g or {}).get("prize", "?"),
                    "ended": bool((g or {}).get("ended")),
                    "end_iso": str((g or {}).get("end_iso", "?")),
                }
            )
    return out


def reminders_view() -> list[dict]:
    state = load_json(REMINDERS_FILE) or {}
    return list((state or {}).get("items", []))
