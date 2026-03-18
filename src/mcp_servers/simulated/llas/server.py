"""
LLAS (Loan/Lease Accounting System) Simulated MCP Server

Simulates the accounting system that manages contract balances, payment history,
account status, and customer profile master data (Phase H).

Tools:
  - get_account(contract_id)                                         → account details
  - get_payment_history(contract_id, limit=12)                       → recent payment history
  - get_balance(contract_id)                                         → current balance and payment status
  - create_account(contract_id, account_data)                        → create account after origination
  - post_payment(contract_id, payment_data)                          → apply a payment
  - get_customer_profile(contract_id)                                → customer profile master data
  - update_customer_profile(contract_id, changes, validated_by, source_system) → update profile (SmartLedger validated only)
  - get_payment_info(contract_id)                                    → payment method details
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from shared.config import get_settings
from shared.logging import configure_logging, get_logger

# ─── Init ─────────────────────────────────────────────────────────────────────

settings = get_settings()
configure_logging("llas", settings.log_level)
logger = get_logger(__name__)

# ─── Module-level state ───────────────────────────────────────────────────────

_accounts: dict[str, dict[str, Any]] = {}
_payment_history: dict[str, list[dict[str, Any]]] = {}
_customer_profiles: dict[str, dict[str, Any]] = {}

# ─── Seed data ────────────────────────────────────────────────────────────────

_SEED_ACCOUNTS: list[dict[str, Any]] = [
    {
        "contract_id": "ORC-2024-001",
        "account_number": "LLAS-2024-001",
        "status": "active",
        "current_balance": 26980.50,
        "next_payment_due": "2026-04-01",
        "next_payment_amount": 487.50,
        "total_paid": 2925.00,
        "payments_made": 6,
        "payments_missed": 0,
        "days_past_due": 0,
        "interest_paid_ytd": 142.30,
        "principal_paid_ytd": 782.70,
    },
    {
        "contract_id": "ORC-2024-002",
        "account_number": "LLAS-2024-002",
        "status": "active",
        "current_balance": 18200.00,
        "next_payment_due": "2026-04-15",
        "next_payment_amount": 349.00,
        "total_paid": 2793.00,
        "payments_made": 8,
        "payments_missed": 0,
        "days_past_due": 0,
        "interest_paid_ytd": 89.00,
        "principal_paid_ytd": 511.00,
    },
    # ORC-2024-003 is in "originated" state — no LLAS account yet (expected for new contracts)
]

_SEED_PAYMENT_HISTORY: dict[str, list[dict[str, Any]]] = {
    "ORC-2024-001": [
        {
            "payment_id": f"PMT-001-{i:03d}",
            "amount": 487.50,
            "payment_date": f"2025-{9 + i:02d}-01",
            "principal": 395.20,
            "interest": 92.30,
            "fees": 0.00,
            "status": "applied",
        }
        for i in range(1, 7)
    ],
    "ORC-2024-002": [
        {
            "payment_id": f"PMT-002-{i:03d}",
            "amount": 349.00,
            "payment_date": f"2025-{7 + i:02d}-15",
            "principal": 281.00,
            "interest": 68.00,
            "fees": 0.00,
            "status": "applied",
        }
        for i in range(1, 9)
    ],
}


# ─── Seed customer profiles (PII — off-chain, in-memory only) ─────────────────

_SEED_CUSTOMER_PROFILES: list[dict[str, Any]] = [
    {
        "contract_id": "ORC-2024-001",
        "address": {
            "street1": "123 Main St",
            "city": "Dallas",
            "state": "TX",
            "zip": "75201",
            "country": "US",
        },
        "contact": {
            "first_name": "James",
            "last_name": "Carter",
            "phone": "214-555-0101",
            "email": "james.carter@example.com",
        },
        "payment_info": {
            "method": "ach",
            "bank_account_last4": "4567",
            "routing_last4": "2345",
            "payment_date": 1,
        },
        "insurance": {
            "carrier": "StateFarm",
            "policy_number": "SF-12345",
            "expiry": "2027-03-01",
        },
        "last_updated_by": "origination",
        "last_updated_at": "2025-09-01T00:00:00+00:00",
    },
    {
        "contract_id": "ORC-2024-002",
        "address": {
            "street1": "456 Oak Ave",
            "city": "Austin",
            "state": "TX",
            "zip": "78701",
            "country": "US",
        },
        "contact": {
            "first_name": "Maria",
            "last_name": "Gonzalez",
            "phone": "512-555-0202",
            "email": "maria.gonzalez@example.com",
        },
        "payment_info": {
            "method": "ach",
            "bank_account_last4": "8901",
            "routing_last4": "6789",
            "payment_date": 15,
        },
        "insurance": {
            "carrier": "AllState",
            "policy_number": "AS-67890",
            "expiry": "2027-06-15",
        },
        "last_updated_by": "origination",
        "last_updated_at": "2025-07-15T00:00:00+00:00",
    },
]


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    for acct in _SEED_ACCOUNTS:
        _accounts[acct["contract_id"]] = acct
    for cid, history in _SEED_PAYMENT_HISTORY.items():
        _payment_history[cid] = list(history)
    for profile in _SEED_CUSTOMER_PROFILES:
        _customer_profiles[profile["contract_id"]] = dict(profile)
    logger.info(
        "llas_seeded",
        account_count=len(_SEED_ACCOUNTS),
        profile_count=len(_SEED_CUSTOMER_PROFILES),
    )
    yield
    logger.info("llas_shutdown")


mcp = FastMCP(
    name="simulated-llas",
    instructions=(
        "Simulated LLAS (Loan/Lease Accounting System). "
        "Provides account balances, payment history, and accounting data for contracts. "
        "Returns found=False for newly originated contracts that have no account yet."
    ),
    lifespan=lifespan,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_account(contract_id: str) -> dict:
    """
    Return LLAS account details for a contract.

    Returns {found: False, contract_id} if no account exists yet —
    this is EXPECTED for newly originated contracts (they have not been activated).

    Returns {found: True, ...account fields...} for active contracts.
    """
    account = _accounts.get(contract_id)
    if account is None:
        logger.info("llas_account_not_found", contract_id=contract_id)
        return {"found": False, "contract_id": contract_id}
    return {"found": True, **account}


@mcp.tool()
async def get_payment_history(contract_id: str, limit: int = 12) -> dict:
    """
    Return recent payment history for a contract (most recent first).

    Returns {found: False} if no account exists for the contract.
    """
    if contract_id not in _accounts:
        return {"found": False, "contract_id": contract_id, "payments": []}

    account = _accounts[contract_id]
    history = _payment_history.get(contract_id, [])
    history_sorted = sorted(history, key=lambda p: p.get("payment_date", ""), reverse=True)

    return {
        "found": True,
        "contract_id": contract_id,
        "account_number": account.get("account_number"),
        "payments": history_sorted[:limit],
        "total_payments": len(history),
    }


@mcp.tool()
async def get_balance(contract_id: str) -> dict:
    """Return current balance and payment status for a contract."""
    account = _accounts.get(contract_id)
    if not account:
        return {"found": False, "contract_id": contract_id}

    return {
        "found": True,
        "contract_id": contract_id,
        "current_balance": account["current_balance"],
        "next_payment_due": account["next_payment_due"],
        "next_payment_amount": account["next_payment_amount"],
        "days_past_due": account["days_past_due"],
        "status": account["status"],
    }


@mcp.tool()
async def post_payment(contract_id: str, payment_data: dict) -> dict:
    """
    Apply a payment to a LLAS account and update the running balance.

    Required keys in payment_data:
      - payment_id (str): identifier from the Payment simulator
      - amount (float): total payment amount collected
      - payment_date (str): ISO date the payment was applied

    Optional keys:
      - principal (float): principal portion (defaults to amount)
      - interest (float): interest portion (defaults to 0.0)
      - fees (float): fee portion (defaults to 0.0)

    Returns: {success, contract_id, account, is_paid_off}
    """
    account = _accounts.get(contract_id)
    if not account:
        return {"success": False, "reason": f"No LLAS account for contract '{contract_id}'"}

    required = ["payment_id", "amount", "payment_date"]
    missing = [f for f in required if f not in payment_data]
    if missing:
        raise ValueError(f"Missing required payment_data fields: {missing}")

    amount = float(payment_data["amount"])
    principal = float(payment_data.get("principal", amount))
    interest = float(payment_data.get("interest", 0.0))
    fees = float(payment_data.get("fees", 0.0))
    payment_id = payment_data["payment_id"]
    payment_date = payment_data["payment_date"]

    # Update account balance
    new_balance = max(0.0, account["current_balance"] - principal)
    account["current_balance"] = round(new_balance, 2)
    account["total_paid"] = round(account.get("total_paid", 0.0) + amount, 2)
    account["payments_made"] = account.get("payments_made", 0) + 1
    account["days_past_due"] = 0  # payment resets delinquency clock
    account["interest_paid_ytd"] = round(account.get("interest_paid_ytd", 0.0) + interest, 2)
    account["principal_paid_ytd"] = round(account.get("principal_paid_ytd", 0.0) + principal, 2)

    is_paid_off = new_balance <= 0.0
    if is_paid_off:
        account["status"] = "paid_off"
        account["next_payment_due"] = None
        account["next_payment_amount"] = 0.0
    else:
        account["status"] = "active"

    # Record in payment history
    if contract_id not in _payment_history:
        _payment_history[contract_id] = []
    _payment_history[contract_id].append({
        "payment_id": payment_id,
        "amount": amount,
        "payment_date": payment_date,
        "principal": principal,
        "interest": interest,
        "fees": fees,
        "status": "applied",
    })

    logger.info(
        "llas_payment_posted",
        contract_id=contract_id,
        payment_id=payment_id,
        amount=amount,
        new_balance=new_balance,
        is_paid_off=is_paid_off,
    )
    return {"success": True, "contract_id": contract_id, "account": account, "is_paid_off": is_paid_off}


@mcp.tool()
async def create_account(contract_id: str, account_data: dict) -> dict:
    """
    Create a new LLAS account for a contract that has just been originated and
    successfully written to the ledger.

    Required keys in account_data:
      - amount_financed (float): opening balance
      - monthly_payment (float)
      - first_payment_date (str): ISO date of first payment due

    Returns: {success, contract_id, account}
    """
    if contract_id in _accounts:
        logger.warning("llas_account_already_exists", contract_id=contract_id)
        return {
            "success": False,
            "reason": f"Account already exists for contract '{contract_id}'",
        }

    required = ["amount_financed", "monthly_payment"]
    missing = [f for f in required if f not in account_data]
    if missing:
        raise ValueError(f"Missing required account_data fields: {missing}")

    account: dict[str, Any] = {
        "contract_id": contract_id,
        "account_number": f"LLAS-{contract_id}",
        "status": "active",
        "current_balance": float(account_data["amount_financed"]),
        "next_payment_due": account_data.get("first_payment_date"),
        "next_payment_amount": float(account_data["monthly_payment"]),
        "total_paid": 0.0,
        "payments_made": 0,
        "payments_missed": 0,
        "days_past_due": 0,
        "interest_paid_ytd": 0.0,
        "principal_paid_ytd": 0.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    _accounts[contract_id] = account
    _payment_history[contract_id] = []

    logger.info("llas_account_created", contract_id=contract_id)
    return {"success": True, "contract_id": contract_id, "account": account}


@mcp.tool()
async def get_customer_profile(contract_id: str) -> dict:
    """
    Return the customer profile master data for a contract.

    The customer profile is the system-of-record for address, contact info,
    payment method, and insurance details. Updated only after SmartLedger validation.

    Returns {found: False} if no profile exists for the contract.
    """
    profile = _customer_profiles.get(contract_id)
    if profile is None:
        return {"found": False, "contract_id": contract_id}
    return {"found": True, **profile}


@mcp.tool()
async def update_customer_profile(
    contract_id: str,
    changes: dict,
    validated_by: str,
    source_system: str,
) -> dict:
    """
    Update the customer profile for a contract.

    IMPORTANT: This tool must only be called by the SmartLedger agent AFTER
    a customer_update record has been successfully written to the ledger.
    The 'validated_by' field must be 'smartledger'.

    Args:
        contract_id:   the contract whose profile is being updated
        changes:       dict of top-level keys (address, contact, payment_info, insurance)
                       with their new values (partial updates — only provided keys updated)
        validated_by:  must be 'smartledger' for this call to proceed
        source_system: source system that originated the change (for audit)

    Returns: {success, contract_id, profile}
    """
    if validated_by != "smartledger":
        return {
            "success": False,
            "reason": "Profile updates must be validated by SmartLedger before applying to LLAS",
        }

    profile = _customer_profiles.get(contract_id)
    if profile is None:
        # Create a new profile (e.g. for newly originated contracts)
        profile = {"contract_id": contract_id}
        _customer_profiles[contract_id] = profile

    # Apply partial updates — only top-level keys present in changes
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(profile.get(key), dict):
            # Deep merge for nested dicts (e.g. address, contact)
            profile[key] = {**profile.get(key, {}), **value}
        else:
            profile[key] = value

    profile["last_updated_by"] = source_system
    profile["last_updated_at"] = datetime.now(timezone.utc).isoformat()

    logger.info(
        "llas_customer_profile_updated",
        contract_id=contract_id,
        source_system=source_system,
        changed_keys=list(changes.keys()),
    )
    return {"success": True, "contract_id": contract_id, "profile": profile}


@mcp.tool()
async def get_payment_info(contract_id: str) -> dict:
    """
    Return payment method details for a contract (last4 only — no full account numbers).

    Returns {found: False} if no profile or payment info exists.
    """
    profile = _customer_profiles.get(contract_id)
    if not profile:
        return {"found": False, "contract_id": contract_id}
    payment_info = profile.get("payment_info")
    if not payment_info:
        return {"found": False, "contract_id": contract_id, "reason": "No payment info on file"}
    return {
        "found": True,
        "contract_id": contract_id,
        "method": payment_info.get("method"),
        "bank_account_last4": payment_info.get("bank_account_last4"),
        "routing_last4": payment_info.get("routing_last4"),
        "payment_date": payment_info.get("payment_date"),
    }


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8012)
