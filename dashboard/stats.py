# dashboard/stats.py
import resource
from datetime import datetime, timezone

from utils import load_json


def rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def overview_stats(bot) -> dict:
    now = datetime.now(timezone.utc)
    uptime_s = max(0.0, (now - bot.start_time).total_seconds())
    loaded = set(bot.extensions.keys())
    cogs = [
        {"name": name, "loaded": f"cogs.{name.lower()}" in loaded}
        for name in sorted(bot.cogs.keys())
    ]
    return {
        "uptime_h": round(uptime_s / 3600, 1),
        "latency_ms": round((bot.latency or 0) * 1000),
        "rss_mb": rss_mb(),
        "guild_count": len(bot.guilds),
        "member_total": sum(g.member_count or 0 for g in bot.guilds),
        "voice_count": sum(len(g.voice_clients or []) for g in bot.guilds),
        "cogs": cogs,
        "command_usage": load_json("data/command_usage.json") or {},
    }
