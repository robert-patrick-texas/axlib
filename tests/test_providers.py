# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Tests for Redis provider lifecycle, TTL, and invalidation behavior."""

from __future__ import annotations

from typing import Any

import pytest

from axlib.credentials.providers import RedisCredentialCache
from axlib.credentials.settings import CredentialSettings


class FakeRedisClient:
    def __init__(self) -> None:
        self.closed = False
        self.hash = {"netuser": "cached-user", "netpass": "cached-pass"}
        self.expirations: list[tuple[str, int]] = []
        self.deleted_fields: list[tuple[str, tuple[str, ...]]] = []

    def ping(self) -> bool:
        return True

    def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        return [self.hash.get(field) for field in fields]

    def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.hash.update(mapping)

    def expire(self, key: str, ttl: int) -> None:
        self.expirations.append((key, ttl))

    def hdel(self, key: str, *fields: str) -> None:
        self.deleted_fields.append((key, fields))
        for field in fields:
            self.hash.pop(field, None)

    def delete(self, key: str) -> None:
        self.hash.clear()

    def close(self) -> None:
        self.closed = True


class FakeRedisModule:
    def __init__(self, client: FakeRedisClient) -> None:
        self.client = client
        self.options: dict[str, object] = {}

    def Redis(self, **options: Any) -> FakeRedisClient:  # noqa: N802
        # Named to match the real `redis` module's `Redis` class, which
        # `RedisCredentialCache` constructs as `self._redis_module.Redis(...)`.
        self.options = options
        return self.client


def test_redis_cache_closes_applies_ttl_and_invalidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeRedisClient()
    module = FakeRedisModule(client)
    monkeypatch.setattr(
        "axlib.credentials.providers._load_redis_module",
        lambda: module,
    )
    settings = CredentialSettings(
        redis_enabled=True,
        redis_tls=True,
        redis_cache_ttl=120,
        redis_key_prefix="axlib",
    )

    with RedisCredentialCache(settings) as cache:
        assert cache.read("operator", ["netuser", "missing"]) == {
            "netuser": "cached-user"
        }
        cache.write("operator", {"netenable": "enable"})
        cache.delete("operator", ["netpass"])

    assert module.options["ssl"] is True
    assert client.expirations == [("axlib:operator", 120)]
    assert client.deleted_fields == [("axlib:operator", ("netpass",))]
    assert client.closed is True
