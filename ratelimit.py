from __future__ import annotations
import threading
import time
from collections import defaultdict, deque

class RateLimiter:

    def __init__(self, limit: int, window: float=60.0, clock=time.monotonic):
        self.limit, self.window, self._clock = (limit, window, clock)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 5000:
                for k in [k for k, v in self._hits.items() if not v][:1000]:
                    self._hits.pop(k, None)
            return True

class DailyCap:

    def __init__(self, cap: int, clock=time.time):
        self.cap, self._clock = (cap, clock)
        self._day, self._count = (self._today(), 0)
        self._lock = threading.Lock()

    def _today(self) -> int:
        return int(self._clock() // 86400)

    def allow(self) -> bool:
        with self._lock:
            today = self._today()
            if today != self._day:
                self._day, self._count = (today, 0)
            if self._count >= self.cap:
                return False
            self._count += 1
            return True
