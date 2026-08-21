# dashboard/logs.py
import logging
from collections import deque

_ring: deque[str] = deque(maxlen=500)
_attached = False


class _RingHandler(logging.Handler):
    def emit(self, record):
        try:
            _ring.append(self.format(record))
        except Exception:
            pass


def attach_log_ring(capacity: int = 500) -> None:
    global _ring, _attached
    if _ring.maxlen != capacity:
        _ring = deque(_ring, maxlen=capacity)
    if not _attached:
        h = _RingHandler()
        h.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(h)
        _attached = True


def recent_logs(n: int = 200) -> list[str]:
    return list(_ring)[-n:]
