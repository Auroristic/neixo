# dashboard/security.py
import time


class RateLimiter:
    """Sliding-window per-key rate limiter (in-memory, single process)."""

    def __init__(self, max_events: int, per_seconds: float):
        self.max = max_events
        self.per = per_seconds
        self.events: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = [t for t in self.events.get(key, []) if now - t < self.per]
        if len(window) >= self.max:
            self.events[key] = window
            return False
        window.append(now)
        self.events[key] = window
        return True


login_limiter = RateLimiter(max_events=10, per_seconds=60.0)
