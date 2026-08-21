# dashboard/boards.py
"""Read-only leaderboard queries across neixo's per-feature SQLite stores."""
import sqlite3
from contextlib import contextmanager

from utils import DB_FILE, _db

SERVERSTATS_DB = "data/serverstats.db"
BUMPS_DB = "data/bumps.db"
REACTIONS_DB = "data/reactions.db"


@contextmanager
def _ro(path: str):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        yield conn
    finally:
        conn.close()


def _table_board(path: str, sql: str, params: tuple = ()) -> list[dict]:
    try:
        with _ro(path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(zip(("user_id", "value"), r)) for r in rows]
    except sqlite3.Error:
        return []


def xp_board(guild_id=None, limit: int = 25) -> list[dict]:
    with _db() as conn:
        if guild_id:
            rows = conn.execute(
                "SELECT user_id, xp FROM user_xp WHERE guild_id = ? "
                "ORDER BY xp DESC LIMIT ?",
                (str(guild_id), limit),
            ).fetchall()
            return [{"user_id": r[0], "value": r[1], "guild_id": str(guild_id)} for r in rows]
        rows = conn.execute(
            "SELECT user_id, SUM(xp) AS total FROM user_xp GROUP BY user_id "
            "ORDER BY total DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"user_id": r[0], "value": r[1]} for r in rows]


def messages_board(guild_id=None, limit: int = 25) -> list[dict]:
    if guild_id:
        return _table_board(
            SERVERSTATS_DB,
            "SELECT user_id, count FROM message_counts WHERE guild_id = ? "
            "ORDER BY count DESC LIMIT ?",
            (str(guild_id), limit),
        )
    return _table_board(
        SERVERSTATS_DB,
        "SELECT user_id, SUM(count) FROM message_counts GROUP BY user_id "
        "ORDER BY 2 DESC LIMIT ?",
        (limit,),
    )


def vc_board(guild_id=None, limit: int = 25) -> list[dict]:
    if guild_id:
        return _table_board(
            SERVERSTATS_DB,
            "SELECT user_id, total_seconds FROM vc_time WHERE guild_id = ? "
            "ORDER BY total_seconds DESC LIMIT ?",
            (str(guild_id), limit),
        )
    return _table_board(
        SERVERSTATS_DB,
        "SELECT user_id, SUM(total_seconds) FROM vc_time GROUP BY user_id "
        "ORDER BY 2 DESC LIMIT ?",
        (limit,),
    )


def bumps_board(guild_id=None, limit: int = 25) -> list[dict]:
    if guild_id:
        return _table_board(
            BUMPS_DB,
            "SELECT user_id, count FROM bump_counts WHERE guild_id = ? "
            "ORDER BY count DESC LIMIT ?",
            (str(guild_id), limit),
        )
    return _table_board(
        BUMPS_DB,
        "SELECT user_id, SUM(count) FROM bump_counts GROUP BY user_id ORDER BY 2 DESC LIMIT ?",
        (limit,),
    )


def reactions_board(guild_id=None, limit: int = 25) -> list[dict]:
    if guild_id:
        return _table_board(
            REACTIONS_DB,
            "SELECT user_id, SUM(count) FROM reaction_stats WHERE guild_id = ? "
            "GROUP BY user_id ORDER BY 2 DESC LIMIT ?",
            (str(guild_id), limit),
        )
    return _table_board(
        REACTIONS_DB,
        "SELECT user_id, SUM(count) FROM reaction_stats GROUP BY user_id "
        "ORDER BY 2 DESC LIMIT ?",
        (limit,),
    )


def fmt_hours(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}m" if h else f"{m}m"
