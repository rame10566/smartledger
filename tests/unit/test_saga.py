"""
Unit tests for agent.core.saga — SagaManager.

All PostgreSQL calls are mocked with AsyncMock so no real DB is needed.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from agent.core.saga import SagaManager
from shared.models.saga import SagaStep


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def saga_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def contract_id() -> str:
    return "ORC-2024-001"


@pytest.fixture
def event_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def mock_conn() -> AsyncMock:
    """A mocked asyncpg connection."""
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock()
    conn.fetch = AsyncMock()
    return conn


@pytest.fixture
def mock_pool(mock_conn) -> MagicMock:
    """A mocked asyncpg pool whose acquire() returns mock_conn as a context manager."""
    pool = MagicMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)
    return pool


@pytest.fixture
def saga(mock_pool, saga_id, contract_id, event_id) -> SagaManager:
    return SagaManager(
        pool=mock_pool,
        saga_id=saga_id,
        contract_id=contract_id,
        event_id=event_id,
        event_type="contract.originated",
        source_system="oracle_los",
    )


# ── TestSagaManagerCheckpoint ─────────────────────────────────────────────────

class TestSagaManagerCheckpoint:
    """Tests for SagaManager.checkpoint()."""

    async def test_checkpoint_inserts_row(self, saga, mock_conn, saga_id, contract_id, event_id):
        """checkpoint() should execute an INSERT into sagas.checkpoints."""
        await saga.checkpoint(SagaStep.EVENT_RECEIVED, status="completed")

        mock_conn.execute.assert_awaited_once()
        sql, *args = mock_conn.execute.call_args[0]
        assert "INSERT INTO sagas.checkpoints" in sql
        assert saga_id in args
        assert contract_id in args
        assert event_id in args
        assert "EVENT_RECEIVED" in args

    async def test_checkpoint_with_payload(self, saga, mock_conn):
        """Payload dict is JSON-serialised before insert."""
        payload = {"los_contract": {"vin": "1HGBH41JXMN109186"}}
        await saga.checkpoint(SagaStep.CONTEXT_GATHERED, payload=payload, status="completed")

        sql, *args = mock_conn.execute.call_args[0]
        # The payload arg should be the JSON string
        payload_arg = next(a for a in args if isinstance(a, str) and "vin" in a)
        assert json.loads(payload_arg) == payload

    async def test_checkpoint_none_payload(self, saga, mock_conn):
        """None payload is passed as None (not serialised to 'null')."""
        await saga.checkpoint(SagaStep.LOCK_ACQUIRED, payload=None, status="completed")

        sql, *args = mock_conn.execute.call_args[0]
        assert None in args

    async def test_checkpoint_accepts_string_step(self, saga, mock_conn):
        """step can be a plain string as well as a SagaStep enum value."""
        await saga.checkpoint("CUSTOM_STEP", status="in_progress")
        sql, *args = mock_conn.execute.call_args[0]
        assert "CUSTOM_STEP" in args

    async def test_checkpoint_default_status_is_completed(self, saga, mock_conn):
        """Default status is 'completed'."""
        await saga.checkpoint(SagaStep.VALIDATED)
        sql, *args = mock_conn.execute.call_args[0]
        assert "completed" in args


class TestSagaManagerComplete:
    """Tests for SagaManager.complete()."""

    async def test_complete_calls_checkpoint_and_mark_idempotent(
        self, saga, mock_conn
    ):
        """complete() should checkpoint COMPLETED and mark the event idempotent."""
        await saga.complete(payload={"record_id": "abc"})

        # Two DB calls: checkpoint INSERT + idempotent INSERT
        assert mock_conn.execute.await_count == 2

        # First call: checkpoints COMPLETED
        first_sql, *first_args = mock_conn.execute.call_args_list[0][0]
        assert "INSERT INTO sagas.checkpoints" in first_sql
        assert "COMPLETED" in first_args

        # Second call: marks idempotent with outcome="written"
        second_sql, *second_args = mock_conn.execute.call_args_list[1][0]
        assert "sagas.processed_events" in second_sql
        assert "written" in second_args


class TestSagaManagerQuarantine:
    """Tests for SagaManager.quarantine()."""

    async def test_quarantine_checkpoints_and_marks_idempotent(self, saga, mock_conn):
        """quarantine() checkpoints QUARANTINED and marks event as 'quarantined'."""
        failures = [{"code": "RULE-SCHEMA-VIN", "message": "VIN invalid"}]
        await saga.quarantine(failures)

        assert mock_conn.execute.await_count == 2

        first_sql, *first_args = mock_conn.execute.call_args_list[0][0]
        assert "INSERT INTO sagas.checkpoints" in first_sql
        assert "QUARANTINED" in first_args

        second_sql, *second_args = mock_conn.execute.call_args_list[1][0]
        assert "sagas.processed_events" in second_sql
        assert "quarantined" in second_args

    async def test_quarantine_stores_failures_in_payload(self, saga, mock_conn):
        """The failures list is stored in the checkpoint payload."""
        failures = [{"code": "RULE-BIZ-AMT-POS", "message": "Amount must be > 0"}]
        await saga.quarantine(failures)

        first_sql, *first_args = mock_conn.execute.call_args_list[0][0]
        payload_arg = next(
            a for a in first_args if isinstance(a, str) and "failures" in a
        )
        payload = json.loads(payload_arg)
        assert payload["failures"] == failures


class TestSagaManagerFail:
    """Tests for SagaManager.fail()."""

    async def test_fail_checkpoints_failed_and_marks_idempotent(self, saga, mock_conn):
        """fail() checkpoints FAILED and marks event as 'failed'."""
        await saga.fail("Unexpected error")

        assert mock_conn.execute.await_count == 2

        first_sql, *first_args = mock_conn.execute.call_args_list[0][0]
        assert "FAILED" in first_args

        second_sql, *second_args = mock_conn.execute.call_args_list[1][0]
        assert "failed" in second_args

    async def test_fail_stores_error_in_payload(self, saga, mock_conn):
        """Error message is stored in the checkpoint payload."""
        await saga.fail("Connection refused")

        first_sql, *first_args = mock_conn.execute.call_args_list[0][0]
        payload_arg = next(
            a for a in first_args if isinstance(a, str) and "error" in a
        )
        payload = json.loads(payload_arg)
        assert payload["error"] == "Connection refused"

    async def test_fail_with_custom_step(self, saga, mock_conn):
        """fail() with explicit step uses that step value."""
        await saga.fail("Oops", step=SagaStep.LEDGER_WRITTEN)

        first_sql, *first_args = mock_conn.execute.call_args_list[0][0]
        assert "LEDGER_WRITTEN" in first_args


class TestSagaManagerIdempotency:
    """Tests for is_already_processed() and mark_idempotent()."""

    async def test_is_already_processed_returns_none_for_new_event(
        self, saga, mock_conn
    ):
        """When no row exists, is_already_processed() returns None."""
        mock_conn.fetchrow.return_value = None

        result = await saga.is_already_processed()
        assert result is None

    async def test_is_already_processed_returns_outcome(self, saga, mock_conn):
        """When row exists, is_already_processed() returns the outcome string."""
        mock_conn.fetchrow.return_value = {"outcome": "written"}

        result = await saga.is_already_processed()
        assert result == "written"

    async def test_is_already_processed_queries_by_event_id(
        self, saga, mock_conn, event_id
    ):
        """Query uses the saga's event_id."""
        mock_conn.fetchrow.return_value = None
        await saga.is_already_processed()

        sql, *args = mock_conn.fetchrow.call_args[0]
        assert "sagas.processed_events" in sql
        assert event_id in args

    async def test_mark_idempotent_inserts_with_on_conflict(self, saga, mock_conn, event_id):
        """mark_idempotent() uses ON CONFLICT DO NOTHING to prevent duplicates."""
        await saga.mark_idempotent("written")

        mock_conn.execute.assert_awaited_once()
        sql, *args = mock_conn.execute.call_args[0]
        assert "sagas.processed_events" in sql
        assert "ON CONFLICT" in sql
        assert "written" in args
        assert event_id in args

    async def test_mark_idempotent_different_outcomes(self, saga, mock_conn):
        """mark_idempotent() is called with the correct outcome for each case."""
        for outcome in ("written", "quarantined", "failed", "skipped"):
            mock_conn.execute.reset_mock()
            await saga.mark_idempotent(outcome)
            sql, *args = mock_conn.execute.call_args[0]
            assert outcome in args


class TestSagaManagerRecovery:
    """Tests for class-method recovery helpers."""

    async def test_get_incomplete_sagas_returns_empty_list(self, mock_pool, mock_conn):
        """When no in-progress sagas exist, returns empty list."""
        mock_conn.fetch.return_value = []
        result = await SagaManager.get_incomplete_sagas(mock_pool)
        assert result == []

    async def test_get_incomplete_sagas_decodes_payload(self, mock_pool, mock_conn):
        """Payload JSON strings are decoded to dicts."""
        mock_conn.fetch.return_value = [
            {
                "saga_id": str(uuid.uuid4()),
                "contract_id": "ORC-2024-001",
                "event_id": str(uuid.uuid4()),
                "step": "CONTEXT_GATHERED",
                "status": "in_progress",
                "payload": '{"los_contract": {"vin": "1HGBH41JXMN109186"}}',
                "created_at": "2026-03-14T10:00:00+00:00",
                "updated_at": "2026-03-14T10:00:01+00:00",
            }
        ]
        result = await SagaManager.get_incomplete_sagas(mock_pool)
        assert len(result) == 1
        assert isinstance(result[0]["payload"], dict)
        assert result[0]["payload"]["los_contract"]["vin"] == "1HGBH41JXMN109186"

    async def test_get_saga_checkpoints_returns_ordered_list(self, mock_pool, mock_conn):
        """get_saga_checkpoints() returns decoded checkpoint rows."""
        saga_id = str(uuid.uuid4())
        mock_conn.fetch.return_value = [
            {
                "saga_id": saga_id,
                "contract_id": "ORC-2024-001",
                "event_id": str(uuid.uuid4()),
                "step": "EVENT_RECEIVED",
                "status": "completed",
                "payload": None,
                "created_at": "2026-03-14T10:00:00+00:00",
            }
        ]
        result = await SagaManager.get_saga_checkpoints(mock_pool, saga_id)
        assert len(result) == 1
        assert result[0]["step"] == "EVENT_RECEIVED"

    async def test_get_saga_checkpoints_queries_by_saga_id(self, mock_pool, mock_conn):
        """Query uses the provided saga_id."""
        saga_id = str(uuid.uuid4())
        mock_conn.fetch.return_value = []
        await SagaManager.get_saga_checkpoints(mock_pool, saga_id)

        sql, *args = mock_conn.fetch.call_args[0]
        assert saga_id in args
