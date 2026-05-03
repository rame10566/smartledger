#!/usr/bin/env python3
"""
SmartLedger — Full Demo Seed
=============================
Seeds the Governance Dashboard with realistic demo data:

  • 3 valid contracts via Oracle LOS (happy path → Contracts page)
  • 1 invalid contract event via Redis direct (interest rate 99.9% → Quarantine Queue)

Usage:
  uv run python scripts/seed_demo.py
  uv run python scripts/seed_demo.py --no-wait   # publish events and exit immediately
  uv run python scripts/seed_demo.py --redis redis://localhost:6379
  uv run python scripts/seed_demo.py --oracle-los http://localhost:8010

Prerequisites:
  docker compose up -d   (full stack must be running)
"""

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import date, datetime, timezone

# ---------------------------------------------------------------------------
# Add src/ so we can use the MCP + redis packages already in the workspace
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import redis.asyncio as aioredis
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# ---------------------------------------------------------------------------
# Config (override via CLI or env vars)
# ---------------------------------------------------------------------------
ORACLE_LOS_URL    = os.getenv("MCP_ORACLE_LOS_URL",    "http://localhost:8010/mcp")
LEDGER_URL        = os.getenv("MCP_LEDGER_URL",        "http://localhost:8002/mcp")
CRM_URL           = os.getenv("MCP_CRM_URL",           "http://localhost:8013/mcp")
PORTAL_URL        = os.getenv("MCP_PORTAL_URL",        "http://localhost:8017/mcp")
INTEGRATION_URL   = os.getenv("MCP_INTEGRATION_URL",   "http://localhost:8022/mcp")
REDIS_URL         = os.getenv("REDIS_URL",             "redis://localhost:6379")


# ---------------------------------------------------------------------------
# Minimal MCP call helper (self-contained — no agent.core import needed)
# ---------------------------------------------------------------------------

async def _mcp_call(url: str, tool: str, arguments: dict):
    """Call an MCP tool over streamable-HTTP. Returns parsed result or None."""
    try:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments)

        if result.isError:
            parts = [item.text for item in (result.content or []) if hasattr(item, "text")]
            raise RuntimeError(f"MCP error: {' | '.join(parts)}")

        if not result.content:
            return None
        text_items = [item.text for item in result.content if hasattr(item, "text")]
        if not text_items:
            return None
        if len(text_items) == 1:
            try:
                return json.loads(text_items[0])
            except json.JSONDecodeError:
                return text_items[0]
        # Multiple TextContent items → FastMCP serialised a list
        items = []
        for t in text_items:
            try:
                items.append(json.loads(t))
            except json.JSONDecodeError:
                items.append(t)
        return items
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"MCP call failed ({url} → {tool}): {e}") from e


# ---------------------------------------------------------------------------
# VIN generator — always valid (17 chars, no I/O/Q)
# ---------------------------------------------------------------------------

def _random_vin() -> str:
    import random
    chars = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
    prefix = "1HGBH41JXM"        # realistic Honda-style prefix (10 chars)
    return prefix + "".join(random.choices(chars, k=7))


# ---------------------------------------------------------------------------
# Contract templates
# ---------------------------------------------------------------------------

HAPPY_PATH_CONTRACTS = [
    {
        "contract_type": "loan",
        "customer": {
            "customer_id": f"CUST-{uuid.uuid4().hex[:6].upper()}",
            "first_name": "Alice", "last_name": "Johnson",
            "email": "alice.johnson@example.com",
            "credit_score": 740, "credit_tier": "prime",
        },
        "vehicle": {
            "vin": _random_vin(),
            "year": 2024, "make": "Toyota", "model": "Camry",
            "trim": "XLE", "color": "Midnight Black",
        },
        "financial_terms": {
            "amount_financed": 28_500.00,
            "term_months": 60,
            "interest_rate": 6.49,
            "monthly_payment": 553.42,
            "down_payment": 3_000.00,
        },
        "dealer_id": "DLR-001",
    },
    {
        "contract_type": "lease",
        "customer": {
            "customer_id": f"CUST-{uuid.uuid4().hex[:6].upper()}",
            "first_name": "Brian", "last_name": "Smith",
            "email": "brian.smith@example.com",
            "credit_score": 780, "credit_tier": "super_prime",
        },
        "vehicle": {
            "vin": _random_vin(),
            "year": 2024, "make": "BMW", "model": "3 Series",
            "trim": "330i xDrive", "color": "Alpine White",
        },
        "financial_terms": {
            "amount_financed": 15_200.00,
            "term_months": 36,
            "interest_rate": 3.99,
            "monthly_payment": 447.00,
            "down_payment": 4_500.00,
        },
        "dealer_id": "DLR-002",
    },
    {
        "contract_type": "loan",
        "customer": {
            "customer_id": f"CUST-{uuid.uuid4().hex[:6].upper()}",
            "first_name": "Carol", "last_name": "Martinez",
            "email": "carol.martinez@example.com",
            "credit_score": 690, "credit_tier": "near_prime",
        },
        "vehicle": {
            "vin": _random_vin(),
            "year": 2024, "make": "Ford", "model": "F-150",
            "trim": "Lariat", "color": "Velocity Blue",
        },
        "financial_terms": {
            "amount_financed": 44_800.00,
            "term_months": 72,
            "interest_rate": 8.49,
            "monthly_payment": 798.21,
            "down_payment": 5_000.00,
        },
        "dealer_id": "DLR-001",
    },
]


# ---------------------------------------------------------------------------
# Publish a contract event that will be QUARANTINED
# (interest_rate=99.9% → RULE-BIZ-RATE fails inside Validation Engine)
# ---------------------------------------------------------------------------

QUARANTINE_CONTRACT = {
    "contract_type": "loan",
    "customer": {
        "customer_id": "CUST-QUARANTINE",
        "first_name": "Dave", "last_name": "Quarantine",
        "email": "dave.q@example.com",
        "credit_score": 500, "credit_tier": "subprime",
    },
    "vehicle": {
        "vin": _random_vin(),
        "year": 2024, "make": "TestCo", "model": "BadRate",
        "trim": "Predatory", "color": "Red",
    },
    "financial_terms": {
        "amount_financed": 20_000.00,
        "term_months": 60,
        "interest_rate": 99.9,       # ← INVALID — triggers RULE-BIZ-RATE
        "monthly_payment": 450.00,
        "down_payment": 0.00,
    },
    "dealer_id": "DLR-001",
}


# ---------------------------------------------------------------------------
# Poll ledger until origination record appears
# ---------------------------------------------------------------------------

async def _seed_charged_off_contract(contract_id: str) -> None:
    """
    Insert a minimal charged-off contract row into PostgreSQL so that
    Scenario E has a real state to validate against.

    Runs via `docker exec smartledger-postgres psql` to avoid local
    PostgreSQL instances intercepting the connection.
    """
    import subprocess

    sql = (
        f"INSERT INTO contracts.state"
        f" (contract_id, current_state, previous_state, state_changed_at, days_past_due, updated_at)"
        f" VALUES ('{contract_id}', 'charged_off', 'active', NOW(), 90, NOW())"
        f" ON CONFLICT (contract_id) DO UPDATE"
        f"   SET current_state = 'charged_off', previous_state = 'active',"
        f"       days_past_due = 90, state_changed_at = NOW(), updated_at = NOW();"
    )
    result = subprocess.run(
        ["docker", "exec", "smartledger-postgres",
         "psql", "-U", "smartledger", "-d", "smartledger", "-c", sql],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql via docker exec failed: {result.stderr.strip()}")


async def _wait_for_integration(integration_ref: str, timeout: int = 20) -> bool:
    """Poll Integration System until the submission reaches 'validated' or 'quarantined'."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        elapsed = timeout - (deadline - time.time())
        sys.stdout.write(f"\r      Polling integration status {integration_ref}... {elapsed:.0f}s/{timeout}s   ")
        sys.stdout.flush()
        await asyncio.sleep(2)
        try:
            status = await _mcp_call(INTEGRATION_URL, "get_integration_status",
                                     {"integration_ref": integration_ref})
            if isinstance(status, dict) and status.get("status") in ("validated", "quarantined", "conflict"):
                sys.stdout.write("\r" + " " * 70 + "\r")
                return True
        except Exception:
            pass
    sys.stdout.write("\r" + " " * 70 + "\r")
    return False


async def _wait_for_ledger(contract_id: str, timeout: int = 30) -> bool:
    """Poll Ledger MCP until an origination record appears. Returns True on success."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        elapsed = timeout - (deadline - time.time())
        sys.stdout.write(f"\r      Polling ledger for {contract_id}... {elapsed:.0f}s/{timeout}s   ")
        sys.stdout.flush()
        await asyncio.sleep(2)
        try:
            lc = await _mcp_call(LEDGER_URL, "get_contract_lifecycle", {"contract_id": contract_id})
            if lc and any(r.get("record_type") == "origination" for r in lc.get("records", [])):
                sys.stdout.write("\r" + " " * 70 + "\r")   # clear line
                return True
        except Exception:
            pass
    sys.stdout.write("\r" + " " * 70 + "\r")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _clean_stale_data(redis_url: str) -> None:
    """Wipe all contract/saga/quarantine data from PostgreSQL and Redis stream."""
    import subprocess

    # Use docker exec to reach the correct container — avoids conflict with any
    # locally-installed Postgres instance that may also be listening on :5432.
    sql = (
        "TRUNCATE contracts.records, contracts.state CASCADE; "
        "TRUNCATE validation.quarantine CASCADE; "
        "TRUNCATE sagas.checkpoints CASCADE; "
        "TRUNCATE audit.log CASCADE; "
        "TRUNCATE validation.used_proof_tokens CASCADE; "
        "TRUNCATE sagas.idempotency CASCADE;"
    )
    print("  Cleaning PostgreSQL tables (via docker exec)...")
    result = subprocess.run(
        ["docker", "exec", "smartledger-postgres",
         "psql", "-U", "smartledger", "-d", "smartledger", "-c", sql],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        # Some tables may not exist yet (first run) — ignore those errors
        if "does not exist" not in result.stderr:
            raise RuntimeError(f"psql failed: {result.stderr.strip()}")
    print("    ✓ PostgreSQL tables truncated")

    print("  Cleaning Redis stream...")
    r = await aioredis.from_url(redis_url, decode_responses=True)
    try:
        # Delete only the event stream key — do NOT FLUSHDB as that also
        # wipes the consumer group, causing the agent to enter a NOGROUP error loop.
        await r.delete("smartledger:events")
        # Clear any per-contract locks
        lock_keys = []
        async for key in r.scan_iter("smartledger:lock:*"):
            lock_keys.append(key)
        if lock_keys:
            await r.delete(*lock_keys)
        print(f"    ✓ Redis stream + {len(lock_keys)} lock(s) cleared")
    finally:
        await r.aclose()


async def _seed_integration_scenarios(wait: bool) -> None:
    """
    Phase H integration scenarios:
      A — Clean CRM address update (ORC-2024-001, James Carter)
      B — Customer Portal payment method update (ORC-2024-001)
      C — CRM + Portal concurrent address conflict (ORC-2024-002, Maria Gonzalez)
      D — Oracle LOS stale sync (ORC-2024-001) → STALE_LOS_SYNC quarantine
      E — Portal payment update on charged-off contract → CONTRACT_STATE_INELIGIBLE
    """
    print("\n── Phase H Integration Scenarios ────────────────────────────────\n")

    # ── Scenario A: Clean CRM address update ─────────────────────────────────
    print("  [A] Clean CRM address update (ORC-2024-001 / James Carter)")
    try:
        sr = await _mcp_call(CRM_URL, "create_service_request", {
            "contract_id":       "ORC-2024-001",
            "customer_id":       "CUST-001",
            "sr_type":           "CONTACT_UPDATE",
            "requested_changes": {
                "address": {
                    "street":  "456 Oak Avenue",
                    "city":    "Dallas",
                    "state":   "TX",
                    "zip":     "75201",
                    "country": "USA",
                }
            },
        })
        sr_id = sr.get("sr_id", "?") if isinstance(sr, dict) else "?"
        print(f"       SR created: {sr_id}")

        complete = await _mcp_call(CRM_URL, "complete_service_request", {
            "sr_id": sr_id,
        })
        int_ref = complete.get("integration_ref", "") if isinstance(complete, dict) else ""
        print(f"       SR completed → integration_ref={int_ref}")

        if wait and int_ref:
            ok = await _wait_for_integration(int_ref)
            status_result = await _mcp_call(INTEGRATION_URL, "get_integration_status",
                                             {"integration_ref": int_ref})
            final = status_result.get("status", "?") if isinstance(status_result, dict) else "?"
            print(f"       Final status: {final}")
    except Exception as e:
        print(f"       ✗ Scenario A failed: {e}")

    await asyncio.sleep(0.5)

    # ── Scenario B: Portal payment method update ──────────────────────────────
    print("\n  [B] Portal payment method update (ORC-2024-001)")
    try:
        portal_result = await _mcp_call(PORTAL_URL, "update_payment_method", {
            "contract_id":  "ORC-2024-001",
            "customer_id":  "CUST-001",
            "changes": {
                "bank_name":       "Acme Bank",
                "account_last4":   "7821",
                "routing_number":  "123456789",
                "payment_day":     15,
            },
        })
        int_ref_b = portal_result.get("integration_ref", "") if isinstance(portal_result, dict) else ""
        print(f"       Portal update submitted → integration_ref={int_ref_b}")

        if wait and int_ref_b:
            await _wait_for_integration(int_ref_b)
            status_result = await _mcp_call(INTEGRATION_URL, "get_integration_status",
                                             {"integration_ref": int_ref_b})
            final = status_result.get("status", "?") if isinstance(status_result, dict) else "?"
            print(f"       Final status: {final}")
    except Exception as e:
        print(f"       ✗ Scenario B failed: {e}")

    await asyncio.sleep(0.5)

    # ── Scenario C: Concurrent conflict — CRM + Portal address update ─────────
    # Both submit address changes to ORC-2024-002 at the same time
    # The second one should detect a CONFLICT_PENDING and both get quarantined
    print("\n  [C] Concurrent conflict: CRM + Portal both update address (ORC-2024-002)")
    try:
        # Source A: CRM submits address update
        sr_c = await _mcp_call(CRM_URL, "create_service_request", {
            "contract_id":       "ORC-2024-002",
            "customer_id":       "CUST-002",
            "sr_type":           "CONTACT_UPDATE",
            "requested_changes": {
                "address": {
                    "street":  "789 River Road",
                    "city":    "Austin",
                    "state":   "TX",
                    "zip":     "73301",
                    "country": "USA",
                }
            },
        })
        sr_id_c = sr_c.get("sr_id", "?") if isinstance(sr_c, dict) else "?"
        complete_c = await _mcp_call(CRM_URL, "complete_service_request", {
            "sr_id": sr_id_c,
        })
        int_ref_c1 = complete_c.get("integration_ref", "") if isinstance(complete_c, dict) else ""
        print(f"       CRM submission → integration_ref={int_ref_c1}")

        # Source B: Portal submits a different address update immediately after
        portal_c = await _mcp_call(PORTAL_URL, "update_contact_info", {
            "contract_id": "ORC-2024-002",
            "customer_id": "CUST-002",
            "changes": {
                "address": {
                    "street":  "100 Congress Ave",
                    "city":    "Austin",
                    "state":   "TX",
                    "zip":     "78701",
                    "country": "USA",
                }
            },
        })
        int_ref_c2 = portal_c.get("integration_ref", "") if isinstance(portal_c, dict) else ""
        print(f"       Portal submission → integration_ref={int_ref_c2}")
        print(f"       (SmartLedger will detect CONFLICT_PENDING — both quarantined)")

        if wait and int_ref_c2:
            await _wait_for_integration(int_ref_c2, timeout=25)
            for iref in [int_ref_c1, int_ref_c2]:
                status_result = await _mcp_call(INTEGRATION_URL, "get_integration_status",
                                                 {"integration_ref": iref})
                final = status_result.get("status", "?") if isinstance(status_result, dict) else "?"
                print(f"       {iref} → {final}")
    except Exception as e:
        print(f"       ✗ Scenario C failed: {e}")

    await asyncio.sleep(0.5)

    # ── Scenario D: Oracle LOS stale sync ────────────────────────────────────
    # ORC-2024-001 was already updated via CRM (Scenario A) moments ago.
    # Simulate Oracle LOS sending a sync with data timestamped from 2020 —
    # clearly older than the validated LLAS record.  Must quarantine STALE_LOS_SYNC.
    print("\n  [D] Oracle LOS stale sync (ORC-2024-001) → expect STALE_LOS_SYNC")
    try:
        sync_result = await _mcp_call(INTEGRATION_URL, "submit_llas_sync", {
            "contract_id":   "ORC-2024-001",
            "source_system": "oracle_los",
            "sync_payload": {
                "contact": {
                    "email": "james.carter.old@example.com",
                    "phone": "555-000-0000",
                },
                "los_updated_at": "2020-01-15T08:00:00Z",   # stale — pre-dates LLAS record
            },
        })
        int_ref_d = sync_result.get("integration_ref", "") if isinstance(sync_result, dict) else ""
        print(f"       Oracle LOS stale sync submitted → integration_ref={int_ref_d}")

        if wait and int_ref_d:
            await _wait_for_integration(int_ref_d)
            status_result = await _mcp_call(INTEGRATION_URL, "get_integration_status",
                                             {"integration_ref": int_ref_d})
            final = status_result.get("status", "?") if isinstance(status_result, dict) else "?"
            print(f"       Final status: {final}  (expected: quarantined / STALE_LOS_SYNC)")
    except Exception as e:
        print(f"       ✗ Scenario D failed (LOS may not have ORC-2024-001): {e}")

    await asyncio.sleep(0.5)

    # ── Scenario E: Payment update on charged-off contract ────────────────────
    # Pre-seed a contract in charged_off state, then submit a payment method
    # change via the Portal.  Validation should reject with
    # CONTRACT_STATE_INELIGIBLE because payment updates are not permitted on
    # accounts that have been charged off.
    _CHARGED_OFF_CONTRACT = "DEMO-CHARGED-OFF-001"
    print(f"\n  [E] Payment update on charged-off contract ({_CHARGED_OFF_CONTRACT})")
    print(f"       → expect CONTRACT_STATE_INELIGIBLE quarantine")
    try:
        await _seed_charged_off_contract(_CHARGED_OFF_CONTRACT)
        print(f"       Seeded {_CHARGED_OFF_CONTRACT} → state=charged_off (90 DPD)")

        # Submit directly to Integration System — portal's contract-customer
        # association check would reject a synthetic contract id, so we go
        # one layer down (still a valid integration flow — LOS or admin tool).
        int_result_e = await _mcp_call(INTEGRATION_URL, "submit_payment_update", {
            "contract_id":   _CHARGED_OFF_CONTRACT,
            "source_system": "customer_portal",
            "changes": {
                "payment_info": {
                    "bank_name":      "Sample Credit Union",
                    "account_last4":  "4321",
                    "routing_number": "987654321",
                    "payment_day":    1,
                }
            },
            "source_ref": f"SEED-E-{uuid.uuid4().hex[:8].upper()}",
        })
        int_ref_e = int_result_e.get("integration_ref", "") if isinstance(int_result_e, dict) else ""
        print(f"       Integration update submitted → integration_ref={int_ref_e}")

        if wait and int_ref_e:
            await _wait_for_integration(int_ref_e)
            status_result = await _mcp_call(INTEGRATION_URL, "get_integration_status",
                                             {"integration_ref": int_ref_e})
            final = status_result.get("status", "?") if isinstance(status_result, dict) else "?"
            print(f"       Final status: {final}  (expected: quarantined / CONTRACT_STATE_INELIGIBLE)")
    except Exception as e:
        print(f"       ✗ Scenario E failed: {e}")

    print()


async def main(wait: bool, clean: bool = False, integration: bool = True) -> None:
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║         SmartLedger — Governance Dashboard Seed             ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")

    # ── Clean stale data if requested ────────────────────────────────────────
    if clean:
        print("── Clean: wiping stale data ─────────────────────────────────────\n")
        try:
            await _clean_stale_data(REDIS_URL)
        except Exception as e:
            print(f"  ✗ Clean failed: {e}")
            print("    Continuing with seeding anyway...\n")
        print()

    # ── Connect to Redis ─────────────────────────────────────────────────────
    try:
        r = await aioredis.from_url(REDIS_URL, decode_responses=True)
        await r.ping()
        print(f"✓ Redis connected  ({REDIS_URL})")
    except Exception as e:
        print(f"✗ Redis unreachable: {e}")
        print("  Make sure the stack is running: docker compose up -d")
        sys.exit(1)

    # ── Originate 3 happy-path contracts via Oracle LOS ─────────────────────
    print(f"\n── Step 1/2: Happy-path contracts (via Oracle LOS) ──────────────\n")

    originated = []
    for i, contract_data in enumerate(HAPPY_PATH_CONTRACTS, 1):
        make = contract_data["vehicle"]["make"]
        mdl  = contract_data["vehicle"]["model"]
        typ  = contract_data["contract_type"].capitalize()
        amt  = contract_data["financial_terms"]["amount_financed"]
        print(f"  [{i}/3] {make} {mdl} — {typ} ${amt:,.0f}")

        try:
            result = await _mcp_call(ORACLE_LOS_URL, "originate_contract",
                                     {"contract_data": contract_data})
        except Exception as e:
            print(f"        ✗ Oracle LOS unreachable: {e}")
            print( "          Is the stack running? Try: docker compose up -d")
            await r.aclose()
            sys.exit(1)

        if result and result.get("success"):
            cid       = result.get("contract_id", "?")
            stream_id = result.get("stream_entry_id", "?")
            print(f"        ✓ Accepted  contract_id={cid}  stream_entry={stream_id}")
            originated.append(cid)
        else:
            print(f"        ✗ Rejected: {result}")

        await asyncio.sleep(0.3)   # stagger to avoid lock contention

    # ── Originate 1 quarantine-triggering contract via Oracle LOS ───────────
    print(f"\n── Step 2/2: Quarantine trigger (invalid interest rate 99.9%) ───\n")
    print(f"  TestCo BadRate — Loan $20,000 (rate=99.9%)")

    try:
        qnt_result = await _mcp_call(ORACLE_LOS_URL, "originate_contract",
                                      {"contract_data": QUARANTINE_CONTRACT})
    except Exception as e:
        print(f"        ✗ Oracle LOS error: {e}")
        await r.aclose()
        sys.exit(1)

    if qnt_result and qnt_result.get("success"):
        qnt_cid = qnt_result.get("contract_id", "?")
        qnt_sid = qnt_result.get("stream_entry_id", "?")
        print(f"        ✓ Accepted  contract_id={qnt_cid}  stream_entry={qnt_sid}")
        print(f"          (agent will quarantine this due to interest_rate=99.9%)")
    else:
        print(f"        ✗ Rejected: {qnt_result}")

    await r.aclose()

    # ── Optionally wait for agent to process happy-path contracts ────────────
    if wait and originated:
        print(f"\n── Waiting for agent to process {len(originated)} contract(s)... ──────\n")
        print(f"   (agent reads from 'smartledger:events' Redis Stream)\n")

        success_count = 0
        for cid in originated:
            ok = await _wait_for_ledger(cid)
            if ok:
                print(f"  ✓ {cid} — origination record confirmed in ledger")
                success_count += 1
            else:
                print(f"  ✗ {cid} — timeout (30s) — check agent logs:")
                print(f"         docker compose logs agent --tail 50")

        if success_count < len(originated):
            print("\n  ⚠  Some contracts did not confirm. The agent may still be processing.")
            print("     Re-run with --no-wait to skip polling, or check logs.")
    else:
        print(f"\n  (Skipping ledger poll — run with --wait to confirm E2E completion)")

    # ── Integration layer scenarios (Phase H) ────────────────────────────────
    if integration:
        await _seed_integration_scenarios(wait)

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║                         Done!                               ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print("║  Open the dashboard:                                        ║")
    print("║    Contracts  → http://localhost:3000/contracts             ║")
    print("║    Quarantine → http://localhost:3000/quarantine            ║")
    print("║    Conflicts  → http://localhost:3000/conflicts             ║")
    print("║    Reports    → http://localhost:3000/reports               ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print("║  Agent logs:                                                ║")
    print("║    docker compose logs agent --follow                       ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed SmartLedger demo data")
    parser.add_argument("--no-wait", dest="wait", action="store_false",
                        default=True,
                        help="Don't poll for confirmation — just publish and exit")
    parser.add_argument("--clean", action="store_true",
                        help="Wipe all stale data (PostgreSQL + Redis) before seeding")
    parser.add_argument("--no-integration", dest="integration", action="store_false",
                        default=True,
                        help="Skip Phase H integration layer scenarios")
    parser.add_argument("--redis", default=REDIS_URL,
                        help=f"Redis URL (default: {REDIS_URL})")
    parser.add_argument("--oracle-los", default=ORACLE_LOS_URL,
                        help=f"Oracle LOS MCP URL (default: {ORACLE_LOS_URL})")
    args = parser.parse_args()

    # Allow CLI overrides to propagate to module-level constants
    ORACLE_LOS_URL = args.oracle_los
    REDIS_URL      = args.redis

    asyncio.run(main(wait=args.wait, clean=args.clean, integration=args.integration))
