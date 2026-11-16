"""Rate limiter jendela geser (sliding window).

Backend memori untuk satu proses. Antarmuka `hit(key, limit, window)` sengaja sederhana agar mudah
diganti ke Redis (mis. ZADD/ZREMRANGEBYSCORE) saat aplikasi dijalankan di beberapa proses/server.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)


def hit(key: str, limit: int, window: float = 60.0) -> tuple[bool, int]:
    """Catat satu permintaan. Kembalikan (diizinkan?, detik sampai boleh lagi)."""
    t = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and t - q[0] >= window:
            q.popleft()
        if len(q) >= limit:
            return False, int(window - (t - q[0])) + 1
        q.append(t)
        return True, 0


def reset():
    with _lock:
        _hits.clear()
