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
Unit tests for the sliding-window rate limiter.

Tests:
  - Requests within the window are allowed.
  - The (n+1)th request is rejected when the window is full.
  - Each user has an independent counter.
  - Retry-After is a positive integer when rate limited.
  - reset() clears the counter for a specific user.
  - rate_limiter respects max_requests from constructor.
"""

import pytest

from chart_summarizer.api.rate_limiter import SlidingWindowRateLimiter


@pytest.fixture
def limiter() -> SlidingWindowRateLimiter:
    """Return a fresh limiter with a small cap for fast tests."""
    return SlidingWindowRateLimiter(max_requests=3, window_seconds=3600)


class TestSlidingWindowRateLimiter:

    async def test_first_request_is_allowed(self, limiter: SlidingWindowRateLimiter) -> None:
        allowed, retry_after = await limiter.check_and_increment("user-A")
        assert allowed is True
        assert retry_after == 0

    async def test_requests_within_limit_are_allowed(self, limiter: SlidingWindowRateLimiter) -> None:
        for _ in range(3):
            allowed, _ = await limiter.check_and_increment("user-B")
            assert allowed is True

    async def test_request_over_limit_is_rejected(self, limiter: SlidingWindowRateLimiter) -> None:
        for _ in range(3):
            await limiter.check_and_increment("user-C")
        allowed, retry_after = await limiter.check_and_increment("user-C")
        assert allowed is False

    async def test_retry_after_is_positive_when_rate_limited(self, limiter: SlidingWindowRateLimiter) -> None:
        for _ in range(3):
            await limiter.check_and_increment("user-D")
        allowed, retry_after = await limiter.check_and_increment("user-D")
        assert allowed is False
        assert retry_after > 0

    async def test_different_users_have_independent_counters(self, limiter: SlidingWindowRateLimiter) -> None:
        # Exhaust user-E
        for _ in range(3):
            await limiter.check_and_increment("user-E")
        e_allowed, _ = await limiter.check_and_increment("user-E")
        assert e_allowed is False

        # user-F is unaffected
        f_allowed, _ = await limiter.check_and_increment("user-F")
        assert f_allowed is True

    async def test_reset_clears_user_counter(self, limiter: SlidingWindowRateLimiter) -> None:
        for _ in range(3):
            await limiter.check_and_increment("user-G")
        blocked_before, _ = await limiter.check_and_increment("user-G")
        assert blocked_before is False

        limiter.reset("user-G")
        allowed_after, _ = await limiter.check_and_increment("user-G")
        assert allowed_after is True

    async def test_reset_all_clears_all_counters(self, limiter: SlidingWindowRateLimiter) -> None:
        await limiter.check_and_increment("user-H")
        await limiter.check_and_increment("user-I")
        limiter.reset()
        h_allowed, _ = await limiter.check_and_increment("user-H")
        i_allowed, _ = await limiter.check_and_increment("user-I")
        assert h_allowed is True
        assert i_allowed is True

    async def test_exact_limit_is_allowed_then_rejected(self, limiter: SlidingWindowRateLimiter) -> None:
        """The nth request (at limit) is allowed; (n+1)th is rejected."""
        results = []
        for _ in range(4):
            allowed, _ = await limiter.check_and_increment("user-J")
            results.append(allowed)
        assert results == [True, True, True, False]