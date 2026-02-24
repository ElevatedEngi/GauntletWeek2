# Copyright (C) 2026 OpenEMR Community
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
In-memory sliding-window rate limiter.

Limits summary generation to RATE_LIMIT_PER_HOUR (default: 20) requests
per user per rolling hour. Uses asyncio.Lock for safe concurrent access.

The limiter is intentionally in-memory — it resets on service restart.
For multi-instance deployments, replace with a Redis-backed implementation.
"""

import asyncio
import time
from collections import defaultdict

from chart_summarizer.config import settings
from chart_summarizer.utils.logging import get_logger

logger = get_logger(__name__)

_WINDOW_SECONDS = 3600  # 1 hour sliding window


class SlidingWindowRateLimiter:
    """
    Per-user sliding-window rate limiter.

    Tracks the timestamps of recent requests in an in-memory dict.
    On each check, entries outside the window are pruned.

    Thread/coroutine safety: protected by asyncio.Lock.
    """

    def __init__(self, max_requests: int, window_seconds: int = _WINDOW_SECONDS) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def check_and_increment(self, user_id: str) -> tuple[bool, int]:
        """
        Check whether user_id is within their rate limit and, if so, record
        the current request.

        Returns:
            (allowed, retry_after_seconds): If allowed is True, the request
            is within quota. If False, retry_after_seconds is the number of
            seconds until the oldest in-window request expires.
        """
        async with self._lock:
            now = time.monotonic()
            window_start = now - self._window

            # Prune expired timestamps
            self._requests[user_id] = [
                ts for ts in self._requests[user_id] if ts > window_start
            ]

            count = len(self._requests[user_id])
            if count >= self._max:
                oldest = self._requests[user_id][0]
                retry_after = int(oldest + self._window - now) + 1
                logger.warning(
                    "RATE_LIMIT | user_id=%s count=%d limit=%d retry_after_s=%d",
                    user_id,
                    count,
                    self._max,
                    retry_after,
                )
                return False, retry_after

            self._requests[user_id].append(now)
            return True, 0

    def reset(self, user_id: str | None = None) -> None:
        """Reset counters — used in tests. Pass None to reset all users."""
        if user_id is None:
            self._requests.clear()
        else:
            self._requests.pop(user_id, None)


# Module-level singleton configured from settings.
# Created lazily so tests can override settings before import.
_limiter: SlidingWindowRateLimiter | None = None


def get_rate_limiter() -> SlidingWindowRateLimiter:
    """Return the module-level rate limiter, creating it on first call."""
    global _limiter
    if _limiter is None:
        _limiter = SlidingWindowRateLimiter(max_requests=settings.RATE_LIMIT_PER_HOUR)
    return _limiter