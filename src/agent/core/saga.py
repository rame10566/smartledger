"""
Saga Pattern — Persistent Checkpoints

Saves saga state to PostgreSQL so the agent can resume after a crash.

Checkpoints (in order for origination flow):
  EVENT_RECEIVED → LOCK_ACQUIRED → CONTEXT_GATHERED →
  VALIDATED → PROOF_TOKEN_ISSUED → LEDGER_WRITTEN → COMPLETED

Usage:
    async with SagaManager(db, saga_id, contract_id, event_id) as saga:
        await saga.checkpoint("EVENT_RECEIVED", payload={...})
        # ... do work ...
        await saga.checkpoint("CONTEXT_GATHERED", payload={...})
"""
# TODO: Implement SagaManager class
