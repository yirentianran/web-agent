"""Unit tests for rate limiter configuration."""

from __future__ import annotations

import pytest
from limits import parse
from slowapi import Limiter
from slowapi.util import get_remote_address


class TestRateLimiterConfiguration:
    def test_limiter_uses_ip_key(self) -> None:
        limiter = Limiter(key_func=get_remote_address)
        assert limiter._key_func is get_remote_address

    def test_default_limit_parsing(self) -> None:
        """Verify the limits library correctly parses standard limit strings."""
        # 60/minute -> 60 hits per 60 seconds
        r = parse("60/minute")
        assert r.amount == 60
        # 1 multiple of a minute = 60 seconds
        assert r.multiples == 1
        assert r.GRANULARITY.seconds == 60

        # 5/minute -> 5 hits per 60 seconds
        r = parse("5/minute")
        assert r.amount == 5
        assert r.multiples == 1

        # 100/hour -> 100 hits per 3600 seconds
        r = parse("100/hour")
        assert r.amount == 100
        assert r.multiples == 1
        assert r.GRANULARITY.seconds == 3600

    def test_limiter_constructor_accepts_default_limits(self) -> None:
        """Verify the Limiter constructor accepts and stores default_limits."""
        limiter = Limiter(
            key_func=get_remote_address, default_limits=["60/minute"]
        )
        # _default_limits should be a non-empty list
        assert isinstance(limiter._default_limits, list)
        assert len(limiter._default_limits) >= 1

    def test_default_limit_provider_string_accessible(self) -> None:
        """Verify the limit provider string can be read from the stored
        LimitGroup objects via their name-mangled private attribute."""
        limiter = Limiter(
            key_func=get_remote_address, default_limits=["60/minute"]
        )
        limit_group = limiter._default_limits[0]
        # LimitGroup uses Python name mangling for __limit_provider
        provider = limit_group._LimitGroup__limit_provider
        assert provider == "60/minute"

    def test_limiter_reset_method_exists(self) -> None:
        """The limiter should have a reset method for test cleanup."""
        limiter = Limiter(
            key_func=get_remote_address, default_limits=["60/minute"]
        )
        assert callable(limiter.reset)
