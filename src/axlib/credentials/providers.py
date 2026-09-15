"""Optional Redis credential cache for Network Operations automation.

Durable credential storage now lives in :mod:`axlib.credentials.file_store` and
:mod:`axlib.credentials.sqlite_store`.  This module contains only the optional
Redis cache, which may temporarily hold plaintext fields to reduce repeated
decryption work on busy automation hosts.  Redis is never treated as the source
of truth.

The redis-py dependency is imported lazily so text-processing tools and local
credential stores remain usable when Redis is not installed.  Production Redis
connections can use ACL authentication, TLS, certificate verification, timeouts,
and a cache TTL.

Example:
    >>> from axlib.credentials.providers import RedisCredentialCache
    >>> from axlib.credentials.settings import CredentialSettings
    >>> settings = CredentialSettings(redis_enabled=False)
    >>> settings.redis_enabled
    False
"""

from __future__ import annotations

from types import ModuleType
from typing import Any, Mapping, Sequence

from .exceptions import (
    CredentialBackendError,
    CredentialDependencyError,
)
from .settings import CredentialSettings


def _load_redis_module() -> ModuleType:
    """Import redis-py lazily so Redis remains an optional runtime feature.

    Args:
        None: The function reads no caller arguments.

    Returns:
        types.ModuleType: Imported :mod:`redis` module.

    Raises:
        CredentialDependencyError: If Redis caching is enabled but redis-py is
            not installed.
    """
    try:
        import redis
    except ImportError as exc:
        raise CredentialDependencyError(
            "Redis credential caching requires the 'redis' package."
        ) from exc
    return redis


class RedisCredentialCache:
    """Context-managed Redis cache for short-lived plaintext credential fields."""

    def __init__(self, settings: CredentialSettings) -> None:
        """Store settings without opening a network connection yet.

        Args:
            settings (CredentialSettings): Redis address, TLS, authentication,
                timeout, namespace, and TTL settings.

        Returns:
            None: Initializers configure the instance in place.

        Raises:
            None: The Redis dependency and connection are checked on entry.
        """
        self.settings = settings
        self._redis_module: ModuleType | None = None
        self._client: Any | None = None

    def _client_options(self) -> dict[str, object]:
        """Build keyword arguments for ``redis.Redis``.

        Args:
            None: Values come from this cache's immutable settings.

        Returns:
            dict[str, object]: redis-py constructor options, including TLS values
                only when TLS is enabled.

        Raises:
            None: Settings were validated before this provider was constructed.
        """
        options: dict[str, object] = {
            "host": self.settings.redis_host,
            "port": self.settings.redis_port,
            "db": self.settings.redis_db,
            "decode_responses": True,
            "socket_connect_timeout": self.settings.redis_connect_timeout,
            "socket_timeout": self.settings.redis_socket_timeout,
        }
        if self.settings.redis_username is not None:
            options["username"] = self.settings.redis_username
        if self.settings.redis_password is not None:
            options["password"] = self.settings.redis_password

        if self.settings.redis_tls:
            options["ssl"] = True
            options["ssl_cert_reqs"] = self.settings.redis_cert_reqs
            if self.settings.redis_ca_certs is not None:
                options["ssl_ca_certs"] = str(self.settings.redis_ca_certs)
            if self.settings.redis_certfile is not None:
                options["ssl_certfile"] = str(self.settings.redis_certfile)
            if self.settings.redis_keyfile is not None:
                options["ssl_keyfile"] = str(self.settings.redis_keyfile)
        return options

    def __enter__(self) -> RedisCredentialCache:
        """Create the Redis client for use inside a ``with`` statement.

        Args:
            None: Provider settings supply all connection options.

        Returns:
            RedisCredentialCache: This connected cache provider.

        Raises:
            CredentialDependencyError: If redis-py is not installed.
            CredentialBackendError: If redis-py cannot construct or ping the
                configured cache endpoint.
        """
        self._redis_module = _load_redis_module()
        try:
            self._client = self._redis_module.Redis(**self._client_options())
            self._client.ping()
        except Exception as exc:
            self.close()
            raise CredentialBackendError(
                f"Unable to connect to Redis credential cache: {exc}"
            ) from exc
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> bool:
        """Close the Redis client regardless of success or failure.

        Args:
            exc_type (type[BaseException] | None): Exception class from the
                managed block, when one occurred.
            exc (BaseException | None): Exception instance from the managed block.
            traceback (object | None): Traceback supplied by Python's context
                manager protocol.

        Returns:
            bool: Always ``False`` so operational errors are never hidden.

        Raises:
            None: Cleanup deliberately suppresses client-close errors.
        """
        del exc_type, exc, traceback
        self.close()
        return False

    def close(self) -> None:
        """Close the current Redis client and clear the reference.

        Args:
            None: The method operates on this provider's client.

        Returns:
            None: Cleanup is performed in place.

        Raises:
            None: A close failure must not mask the lookup error that triggered
                cleanup.
        """
        if self._client is None:
            return
        try:
            self._client.close()
        except Exception:
            # Redis close errors are not actionable after a lookup and should not
            # obscure a more useful connection or decryption error.
            pass
        finally:
            self._client = None

    def _require_client(self) -> Any:
        """Return the active client or explain the context-manager requirement.

        Args:
            None: The method inspects internal provider state.

        Returns:
            Any: Active redis-py client.

        Raises:
            CredentialBackendError: If a caller forgot to enter the cache with a
                ``with`` statement.
        """
        if self._client is None:
            raise CredentialBackendError(
                "RedisCredentialCache must be used inside a 'with' statement."
            )
        return self._client

    def _cache_key(self, service: str) -> str:
        """Build the Redis hash key while preserving legacy service-only keys.

        Args:
            service (str): Normalized service name used by existing axlib data.

        Returns:
            str: ``service`` by default, or ``prefix:service`` when an operator
                configures a namespace.

        Raises:
            None: The function performs deterministic string formatting only.
        """
        prefix = self.settings.redis_key_prefix
        return f"{prefix}:{service}" if prefix else service

    def read(self, service: str, fields: Sequence[str]) -> dict[str, str]:
        """Read selected values from a Redis hash.

        Args:
            service (str): Normalized service key identifying an operator or
                shared automation account.
            fields (Sequence[str]): Credential fields to retrieve.

        Returns:
            dict[str, str]: Non-``None`` cached values keyed by field name.

        Raises:
            CredentialBackendError: If Redis rejects or times out the operation.
        """
        if not fields:
            return {}
        client = self._require_client()
        try:
            values = client.hmget(self._cache_key(service), list(fields))
        except Exception as exc:
            raise CredentialBackendError(
                f"Unable to read Redis credentials for service {service!r}: {exc}"
            ) from exc
        return {
            field: value
            for field, value in zip(fields, values, strict=True)
            if value is not None
        }

    def delete(self, service: str, fields: Sequence[str] | None = None) -> None:
        """Remove cached fields after a credential change or rotation.

        Args:
            service (str): Normalized service key whose cache must be invalidated.
            fields (Sequence[str] | None): Specific hash fields to remove.  When
                omitted or empty, the complete service hash is deleted.

        Returns:
            None: Redis is modified in place.

        Raises:
            CredentialBackendError: If Redis rejects or times out the delete.
        """
        client = self._require_client()
        key = self._cache_key(service)
        try:
            if fields:
                client.hdel(key, *fields)
            else:
                client.delete(key)
        except Exception as exc:
            raise CredentialBackendError(
                f"Unable to invalidate Redis credentials for service {service!r}: "
                f"{exc}"
            ) from exc

    def write(self, service: str, values: Mapping[str, str]) -> None:
        """Cache encrypted-file values and apply the configured expiration.

        Args:
            service (str): Normalized service key used as the Redis hash key.
            values (Mapping[str, str]): Plaintext fields to cache temporarily.

        Returns:
            None: Redis is modified in place.

        Raises:
            CredentialBackendError: If Redis rejects or times out the write.
        """
        if not values:
            return
        client = self._require_client()
        key = self._cache_key(service)
        try:
            client.hset(key, mapping=dict(values))
            if self.settings.redis_cache_ttl > 0:
                # Expiring plaintext limits the exposure window if a cache host
                # is inspected, while a zero TTL remains available for legacy
                # deployments that intentionally manage expiration elsewhere.
                client.expire(key, self.settings.redis_cache_ttl)
        except Exception as exc:
            raise CredentialBackendError(
                f"Unable to write Redis credentials for service {service!r}: {exc}"
            ) from exc
