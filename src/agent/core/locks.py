"""
Per-Contract Distributed Lock (Redis SET NX PX)

Ensures only ONE agent instance processes a given contract at a time.
Parallel processing is allowed across different contracts simultaneously.

Redis key: contract:{contract_id}
Redis value: saga_id  (identifies which saga holds the lock — aids debugging)
TTL: configurable, default 60 000 ms

On lock failure: raises LockAcquisitionError → the event is NOT ACK'd
and will be retried by the same or another consumer after the TTL expires.

Release safety: uses a Lua script to only delete the key if the stored
value still matches saga_id, preventing accidental release of a lock that
was acquired by a different saga after TTL expiry.
"""

import uuid
from typing import Any

from shared.logging import get_logger

logger = get_logger(__name__)

# Lua script: delete the lock key only if its value equals the caller's saga_id.
# This prevents a slow saga from releasing a lock acquired by a newer saga.
_LUA_RELEASE = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

_LOCK_KEY_PREFIX = "contract:"


class LockAcquisitionError(Exception):
    """Raised when the per-contract lock cannot be acquired (already held)."""
    pass


class ContractLock:
    """
    Async context manager — acquires and releases a per-contract Redis lock.

    Usage:
        lock = ContractLock(redis_client, contract_id, saga_id)
        async with lock:
            # Only one coroutine per contract_id can be inside here
            ...

    Raises LockAcquisitionError if the lock is already held by another saga.
    """

    def __init__(
        self,
        redis: Any,
        contract_id: str,
        saga_id: str | uuid.UUID,
        ttl_ms: int = 60_000,
    ) -> None:
        self.redis = redis
        self.contract_id = contract_id
        self.saga_id = str(saga_id)
        self.ttl_ms = ttl_ms
        self.key = f"{_LOCK_KEY_PREFIX}{contract_id}"
        self._acquired = False

    async def acquire(self) -> None:
        """
        Try to acquire the lock via SET NX PX.
        Raises LockAcquisitionError if not acquired within one attempt.
        """
        result = await self.redis.set(
            self.key,
            self.saga_id,
            nx=True,       # only set if key does NOT exist
            px=self.ttl_ms,  # auto-expire in ttl_ms milliseconds
        )
        if not result:
            # Reveal who holds it (for logging)
            holder = await self.redis.get(self.key)
            raise LockAcquisitionError(
                f"Contract '{self.contract_id}' is already locked by saga '{holder}'. "
                f"TTL: {self.ttl_ms}ms."
            )
        self._acquired = True
        logger.info(
            "lock_acquired",
            contract_id=self.contract_id,
            saga_id=self.saga_id,
            ttl_ms=self.ttl_ms,
        )

    async def release(self) -> None:
        """Release the lock using a safe Lua compare-and-delete script."""
        if not self._acquired:
            return
        result = await self.redis.eval(_LUA_RELEASE, 1, self.key, self.saga_id)
        self._acquired = False
        if result:
            logger.info("lock_released", contract_id=self.contract_id, saga_id=self.saga_id)
        else:
            logger.warning(
                "lock_release_skipped_not_owner",
                contract_id=self.contract_id,
                saga_id=self.saga_id,
            )

    async def extend(self, extra_ms: int | None = None) -> bool:
        """
        Extend the lock TTL for long-running steps.
        Only succeeds if this saga still holds the lock.
        Returns True if extended, False if lock was lost.
        """
        extend_ms = extra_ms or self.ttl_ms
        current_holder = await self.redis.get(self.key)
        if current_holder != self.saga_id:
            logger.warning(
                "lock_extend_failed_not_owner",
                contract_id=self.contract_id,
                saga_id=self.saga_id,
                current_holder=current_holder,
            )
            return False
        await self.redis.pexpire(self.key, extend_ms)
        return True

    async def __aenter__(self) -> "ContractLock":
        await self.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type | None,
        exc_val: Exception | None,
        exc_tb: Any,
    ) -> bool:
        await self.release()
        return False  # never suppress exceptions
