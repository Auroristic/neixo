# dashboard/stats.py
import logging
import resource
from datetime import datetime, timezone

from utils import load_json

log = logging.getLogger(__name__)


def rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def lavalink_nodes() -> list[dict]:
    """Best-effort snapshot of wavelink nodes; never raises."""
    try:
        import wavelink

        out = []
        for node in dict(getattr(wavelink.Pool, "nodes", {})).values():
            available = bool(
                getattr(node, "_available", False)
                or getattr(node, "status", False)
            )
            out.append(
                {
                    "name": getattr(node, "identifier", None) or "lavalink",
                    "available": available,
                    "players": len(getattr(node, "players", {}) or {}),
                }
            )
        return out
    except Exception:
        log.debug("lavalink snapshot failed", exc_info=True)
        return []


def overview_stats(bot) -> dict:
    now = datetime.now(timezone.utc)
    uptime_s = max(0.0, (now - bot.start_time).total_seconds())
    loaded_exts = set(bot.extensions.keys())
    cogs = [
        {"name": name, "loaded": type(cog).__module__ in loaded_exts}
        for name, cog in sorted(bot.cogs.items())
    ]
    return {
        "uptime_h": round(uptime_s / 3600, 1),
        "latency_ms": round((bot.latency or 0) * 1000),
        "rss_mb": rss_mb(),
        "guild_count": len(bot.guilds),
        "member_total": sum(g.member_count or 0 for g in bot.guilds),
        "voice_count": sum(1 for g in bot.guilds if getattr(g, "voice_client", None)),
        "cogs": cogs,
        "command_usage": load_json("data/command_usage.json") or {},
        "lavalink": lavalink_nodes(),
    }
