"""
Unit tests for agent.core.locks — ContractLock (Redis SET NX PX).

All Redis calls are mocked with AsyncMock so no real Redis is needed.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.locks import ContractLock, LockAcquisitionError


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def saga_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def contract_id() -> str:
    return "ORC-2024-TEST"


@pytest.fixture
def mock_redis() -> AsyncMock:
    """A fully mocked async Redis client."""
    r = AsyncMock()
    return r


# ── TestContractLock ──────────────────────────────────────────────────────────

class TestContractLockAcquire:
    """Tests for ContractLock.acquire()."""

    async def test_acquire_success(self, mock_redis, contract_id, saga_id):
        """SET NX returns truthy value → lock acquired."""
        mock_redis.set.return_value = True

        lock = ContractLock(mock_redis, contract_id, saga_id)
        await lock.acquire()

        mock_redis.set.assert_awaited_once_with(
            f"contract:{contract_id}",
            saga_id,
            nx=True,
            px=60_000,
        )
        assert lock._acquired is True

    async def test_acquire_failure_raises(self, mock_redis, contract_id, saga_id):
        """SET NX returns None/False → LockAcquisitionError raised."""
        mock_redis.set.return_value = None
        mock_redis.get.return_value = "other-saga-id"

        lock = ContractLock(mock_redis, contract_id, saga_id)
        with pytest.raises(LockAcquisitionError) as exc_info:
            await lock.acquire()

        assert contract_id in str(exc_info.value)
        assert lock._acquired is False

    async def test_acquire_uses_custom_ttl(self, mock_redis, contract_id, saga_id):
        """Custom TTL is passed through to Redis."""
        mock_redis.set.return_value = True

        lock = ContractLock(mock_redis, contract_id, saga_id, ttl_ms=30_000)
        await lock.acquire()

        mock_redis.set.assert_awaited_once_with(
            f"contract:{contract_id}",
            saga_id,
            nx=True,
            px=30_000,
        )

    async def test_lock_key_format(self, mock_redis, saga_id):
        """Lock key follows the contract:{id} format."""
        mock_redis.set.return_value = True
        lock = ContractLock(mock_redis, "ABC-123", saga_id)
        await lock.acquire()
        call_args = mock_redis.set.call_args
        assert call_args[0][0] == "contract:ABC-123"

    async def test_uuid_saga_id_converted_to_str(self, mock_redis, contract_id):
        """UUID saga_id is converted to string before use."""
        mock_redis.set.return_value = True
        saga_uuid = uuid.uuid4()
        lock = ContractLock(mock_redis, contract_id, saga_uuid)
        await lock.acquire()
        # value stored should be string, not UUID object
        call_args = mock_redis.set.call_args
        assert isinstance(call_args[0][1], str)
        assert call_args[0][1] == str(saga_uuid)


class TestContractLockRelease:
    """Tests for ContractLock.release()."""

    async def test_release_success(self, mock_redis, contract_id, saga_id):
        """Lua script returns 1 → lock released cleanly."""
        mock_redis.set.return_value = True
        mock_redis.eval.return_value = 1

        lock = ContractLock(mock_redis, contract_id, saga_id)
        await lock.acquire()
        await lock.release()

        # Lua script called with correct key and saga_id
        mock_redis.eval.assert_awaited_once()
        args = mock_redis.eval.call_args[0]
        assert args[1] == 1                          # numkeys
        assert args[2] == f"contract:{contract_id}"  # KEYS[1]
        assert args[3] == saga_id                    # ARGV[1]
        assert lock._acquired is False

    async def test_release_not_owner(self, mock_redis, contract_id, saga_id):
        """Lua script returns 0 → lock was already released/taken, no error raised."""
        mock_redis.set.return_value = True
        mock_redis.eval.return_value = 0

        lock = ContractLock(mock_redis, contract_id, saga_id)
        await lock.acquire()
        # Should not raise even when Lua returns 0
        await lock.release()
        assert lock._acquired is False

    async def test_release_without_acquire_is_noop(self, mock_redis, contract_id, saga_id):
        """Calling release() without acquire() does nothing."""
        lock = ContractLock(mock_redis, contract_id, saga_id)
        await lock.release()
        mock_redis.eval.assert_not_awaited()


class TestContractLockContextManager:
    """Tests for async context manager behaviour."""

    async def test_context_manager_acquires_and_releases(self, mock_redis, contract_id, saga_id):
        """Entering/exiting the context manager acquires and releases the lock."""
        mock_redis.set.return_value = True
        mock_redis.eval.return_value = 1

        lock = ContractLock(mock_redis, contract_id, saga_id)
        async with lock:
            assert lock._acquired is True

        assert lock._acquired is False
        mock_redis.eval.assert_awaited_once()

    async def test_context_manager_releases_on_exception(self, mock_redis, contract_id, saga_id):
        """Lock is released even when an exception is raised inside the block."""
        mock_redis.set.return_value = True
        mock_redis.eval.return_value = 1

        lock = ContractLock(mock_redis, contract_id, saga_id)
        with pytest.raises(ValueError):
            async with lock:
                raise ValueError("something went wrong")

        assert lock._acquired is False
        mock_redis.eval.assert_awaited_once()

    async def test_context_manager_propagates_exception(self, mock_redis, contract_id, saga_id):
        """Exceptions raised inside the block are NOT suppressed."""
        mock_redis.set.return_value = True
        mock_redis.eval.return_value = 1

        with pytest.raises(RuntimeError, match="boom"):
            async with ContractLock(mock_redis, contract_id, saga_id):
                raise RuntimeError("boom")

    async def test_context_manager_lock_failure_raises(self, mock_redis, contract_id, saga_id):
        """If acquire() fails, LockAcquisitionError propagates from __aenter__."""
        mock_redis.set.return_value = None
        mock_redis.get.return_value = "other"

        with pytest.raises(LockAcquisitionError):
            async with ContractLock(mock_redis, contract_id, saga_id):
                pass  # should not reach here


class TestContractLockExtend:
    """Tests for ContractLock.extend()."""

    async def test_extend_success(self, mock_redis, contract_id, saga_id):
        """When the lock is still owned, PEXPIRE is called and True returned."""
        mock_redis.set.return_value = True
        mock_redis.get.return_value = saga_id
        mock_redis.pexpire.return_value = 1

        lock = ContractLock(mock_redis, contract_id, saga_id)
        await lock.acquire()
        result = await lock.extend()

        assert result is True
        mock_redis.pexpire.assert_awaited_once_with(f"contract:{contract_id}", 60_000)

    async def test_extend_not_owner(self, mock_redis, contract_id, saga_id):
        """When another saga holds the lock, extend returns False and no PEXPIRE called."""
        mock_redis.set.return_value = True
        mock_redis.get.return_value = "different-saga"

        lock = ContractLock(mock_redis, contract_id, saga_id)
        await lock.acquire()
        result = await lock.extend()

        assert result is False
        mock_redis.pexpire.assert_not_awaited()

    async def test_extend_custom_ttl(self, mock_redis, contract_id, saga_id):
        """Custom extra_ms is passed to PEXPIRE."""
        mock_redis.set.return_value = True
        mock_redis.get.return_value = saga_id
        mock_redis.pexpire.return_value = 1

        lock = ContractLock(mock_redis, contract_id, saga_id)
        await lock.acquire()
        await lock.extend(extra_ms=120_000)

        mock_redis.pexpire.assert_awaited_once_with(f"contract:{contract_id}", 120_000)
