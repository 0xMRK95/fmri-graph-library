from __future__ import annotations

import time
import random


class TokenBucket:
    def __init__(self, rate_per_min: int, capacity: int | None = None) -> None:
        self.rate_per_sec = rate_per_min / 60.0
        self.capacity = capacity or max(1, rate_per_min // 2)
        self.tokens = self.capacity
        self.last = time.time()

    def take(self, tokens: int = 1) -> None:
        now = time.time()
        elapsed = now - self.last
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_sec)
        self.last = now
        while self.tokens < tokens:
            sleep_for = (tokens - self.tokens) / self.rate_per_sec
            time.sleep(sleep_for)
            now = time.time()
            elapsed = now - self.last
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_sec)
            self.last = now
        self.tokens -= tokens


def backoff_sleep(attempt: int, base: float = 0.5, cap: float = 8.0) -> None:
    delay = min(cap, base * (2 ** attempt))
    delay = delay * (0.5 + random.random())
    time.sleep(delay)
