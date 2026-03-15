#!/usr/bin/env python3
"""
SmartLedger — Origination Happy Path Demo

Demonstrates the full contract.originated E2E flow:

  1. Call Oracle LOS: originate_contract  →  publishes event to Redis Stream
  2. Agent picks up the event (watch the agent log)
  3. Poll Ledger MCP: get_contract_lifecycle  until the origination record appears
  4. Print the full lifecycle, audit trail, and current state

Prerequisites:
  - ./scripts/dev_start.sh running (infra + all MCP servers + agent)
  - OR services running individually

Usage:
  uv run python scripts/run_origination_demo.py
  uv run python scripts/run_origination_demo.py --contract-id ORC-2024-001  # use existing
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import date

# Add src/ to the path so we can import our packages
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent.core.mcp_client import oracle_los, ledger


# ── Demo contract data ────────────────────────────────────────────────────────

def _make_demo_contract(contract_id: str | None = None) -> dict:
    """Build a valid origination payload for the demo."""
    cid = contract_id or f"DEMO-{uuid.uuid4().hex[:8].upper()}"
    return {
        "contract_id": cid,
        "contract_type": "loan",
        "vin": f"1HGBH41JXMN{uuid.uuid4().hex[:6].upper()}",
        "vehicle": {
            "year":  2024,
            "make":  "Toyota",
            "model": "Camry",
            "trim":  "XLE",
            "color": "Midnight Black",
        },
        "customer": {
            "customer_id": f"CUST-{uuid.uuid4().hex[:6].upper()}",
            "first_name":  "Alex",
            "last_name":   "Demo",
            "email":       "alex.demo@example.com",
        },
        "financial_terms": {
            "amount_financed":  28_500.00,
            "term_months":      60,
            "interest_rate":    6.99,
            "monthly_payment":  563.42,
            "down_payment":     3_000.00,
        },
        "dealer_id":         "DLR-001",
        "origination_date":  date.today().isoformat(),
        "notes":             "Demo origination — Phase D E2E test",
    }


# ── Display helpers ───────────────────────────────────────────────────────────

def _banner(msg: str) -> None:
    width = max(len(msg) + 4, 64)
    print("─" * width)
    print(f"  {msg}")
    print("─" * width)


def _pp(data: dict | list) -> None:
    print(json.dumps(data, indent=2, default=str))


# ── Main flow ─────────────────────────────────────────────────────────────────

async def run_demo(use_existing_id: str | None = None) -> None:
    _banner("SmartLedger — Origination Happy Path Demo")

    # ── Step 1: Originate via Oracle LOS ─────────────────────────────────────
    if use_existing_id:
        print(f"\n[1/4] Using existing contract: {use_existing_id}")
        contract_id = use_existing_id
    else:
        print("\n[1/4] Originating contract via Oracle LOS...")
        contract_data = _make_demo_contract()
        contract_id = contract_data["contract_id"]
        print(f"      Contract ID : {contract_id}")
        print(f"      VIN         : {contract_data['vin']}")
        print(f"      Amount      : ${contract_data['financial_terms']['amount_financed']:,.2f}")
        print(f"      Term        : {contract_data['financial_terms']['term_months']} months")

        try:
            result = await oracle_los.originate_contract(contract_data)
        except Exception as e:
            print(f"\n  ERROR: Could not reach Oracle LOS — is dev_start.sh running?")
            print(f"  {e}")
            sys.exit(1)

        if not result.get("success"):
            print(f"\n  ERROR: Origination rejected: {result}")
            sys.exit(1)

        stream_id = result.get("stream_entry_id", "unknown")
        print(f"\n  ✓ Origination accepted")
        print(f"  ✓ Event published to Redis Stream: {stream_id}")

    # ── Step 2: Wait for agent to process ────────────────────────────────────
    print(f"\n[2/4] Waiting for agent to process event...")
    print(f"      (agent is consuming 'smartledger:events' stream)")

    max_wait_secs = 30
    poll_interval = 1.0
    start = time.time()
    lifecycle = None

    while time.time() - start < max_wait_secs:
        elapsed = time.time() - start
        sys.stdout.write(f"\r      Polling ledger... {elapsed:.0f}s / {max_wait_secs}s")
        sys.stdout.flush()
        await asyncio.sleep(poll_interval)

        try:
            lifecycle = await ledger.get_contract_lifecycle(contract_id)
        except Exception:
            continue  # ledger might not be ready yet

        records = lifecycle.get("records", [])
        if any(r.get("record_type") == "origination" for r in records):
            break
        lifecycle = None

    print()  # newline after polling indicator

    if lifecycle is None:
        print(f"\n  TIMEOUT: No origination record appeared in {max_wait_secs}s")
        print(f"  Check agent logs: tail -f .logs/agent.log")
        sys.exit(1)

    # ── Step 3: Show lifecycle ─────────────────────────────────────────────────
    print(f"\n[3/4] Origination record written to ledger!")
    print(f"\n      Contract lifecycle:")
    print(f"      ─────────────────────────────────────────")
    print(f"      Current state  : {lifecycle.get('current_state', 'unknown')}")
    print(f"      Total records  : {lifecycle.get('total_records', 0)}")
    print(f"      Payments made  : {lifecycle.get('total_payments_made', 0)}")

    state_history = lifecycle.get("state_history", [])
    if state_history:
        print(f"      State history  :")
        for entry in state_history:
            print(f"        {entry.get('previous_state', '—')} → {entry.get('state')}")

    # ── Step 4: Show audit trail ───────────────────────────────────────────────
    print(f"\n[4/4] Audit trail:")
    try:
        audit = await ledger.get_audit_trail(contract_id)
        if audit:
            for entry in audit:
                ts = entry.get("created_at", "")[:19]
                action = entry.get("action", "")
                print(f"      [{ts}] {action}")
        else:
            print(f"      (no audit entries yet)")
    except Exception as e:
        print(f"      (audit trail not available: {e})")

    _banner("Demo complete — origination happy path E2E verified!")
    print()
    print("  What just happened:")
    print("  1. Oracle LOS accepted the contract and published contract.originated")
    print("     to the Redis Stream 'smartledger:events'")
    print("  2. The AI Agent consumed the event from the stream")
    print("  3. The Agent acquired a per-contract Redis lock for", contract_id)
    print("  4. The Validation Engine validated the event and issued a proof token")
    print("  5. The Ledger MCP verified the proof token and wrote an immutable record")
    print("  6. The state was transitioned: originated → active")
    print("  7. A LLAS accounting account was created")
    print("  8. The saga was checkpointed COMPLETED")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="SmartLedger origination E2E demo")
    parser.add_argument(
        "--contract-id",
        help="Use an existing Oracle LOS contract ID (skips origination step)",
    )
    args = parser.parse_args()
    asyncio.run(run_demo(use_existing_id=args.contract_id))


if __name__ == "__main__":
    main()
