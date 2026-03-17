"""
Rules Engine Simulated MCP Server

Simulates an enterprise Rules Engine that evaluates loan/lease eligibility
based on credit score, LTV ratio, debt-to-income ratio, vehicle age, and
other business criteria.

Credit Tiers:
  super_prime   — 780+   — best terms, highest LTV, longest terms
  prime         — 720-779 — standard terms
  near_prime    — 680-719 — slightly restricted
  subprime      — 620-679 — restricted terms, lower LTV caps
  deep_subprime — <620    — most restricted, short terms only

Tools:
  - evaluate_eligibility(application)  → pass/fail + individual rule results
  - get_credit_tier(credit_score)      → tier name + tier config
  - get_rule_set(contract_type?)       → all active rules for a contract type
  - list_rule_sets()                   → available rule set names
"""

from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from shared.config import get_settings
from shared.logging import configure_logging, get_logger

# ─── Init ─────────────────────────────────────────────────────────────────────

settings = get_settings()
configure_logging("rules-engine", settings.log_level)
logger = get_logger(__name__)

# ─── Credit Tier Definitions ─────────────────────────────────────────────────

_CREDIT_TIERS: dict[str, dict[str, Any]] = {
    "super_prime": {
        "tier": "super_prime",
        "min_score": 780,
        "max_score": 850,
        "description": "Excellent credit — best available terms",
        "max_ltv_loan": 1.20,       # can finance up to 120% of vehicle value
        "max_ltv_lease": 1.10,
        "max_term_loan": 84,        # months
        "max_term_lease": 48,
        "max_dti": 0.50,            # 50% debt-to-income ratio
        "max_vehicle_age_years": 10,
        "min_down_payment_pct": 0.0, # no down payment required
    },
    "prime": {
        "tier": "prime",
        "min_score": 720,
        "max_score": 779,
        "description": "Good credit — standard terms",
        "max_ltv_loan": 1.10,
        "max_ltv_lease": 1.00,
        "max_term_loan": 72,
        "max_term_lease": 48,
        "max_dti": 0.45,
        "max_vehicle_age_years": 8,
        "min_down_payment_pct": 0.0,
    },
    "near_prime": {
        "tier": "near_prime",
        "min_score": 680,
        "max_score": 719,
        "description": "Fair credit — slightly restricted terms",
        "max_ltv_loan": 1.00,
        "max_ltv_lease": 0.95,
        "max_term_loan": 72,
        "max_term_lease": 36,
        "max_dti": 0.40,
        "max_vehicle_age_years": 7,
        "min_down_payment_pct": 0.05,  # 5% minimum down
    },
    "subprime": {
        "tier": "subprime",
        "min_score": 620,
        "max_score": 679,
        "description": "Below average credit — restricted terms",
        "max_ltv_loan": 0.90,
        "max_ltv_lease": 0.85,
        "max_term_loan": 60,
        "max_term_lease": 36,
        "max_dti": 0.35,
        "max_vehicle_age_years": 5,
        "min_down_payment_pct": 0.10,  # 10% minimum down
    },
    "deep_subprime": {
        "tier": "deep_subprime",
        "min_score": 300,
        "max_score": 619,
        "description": "Poor credit — most restrictive terms",
        "max_ltv_loan": 0.80,
        "max_ltv_lease": 0.0,  # no lease for deep subprime
        "max_term_loan": 48,
        "max_term_lease": 0,   # lease not available
        "max_dti": 0.30,
        "max_vehicle_age_years": 3,
        "min_down_payment_pct": 0.20,  # 20% minimum down
    },
}

# Tier lookup sorted by min_score descending for fast classification
_TIER_THRESHOLDS = sorted(
    _CREDIT_TIERS.values(),
    key=lambda t: t["min_score"],
    reverse=True,
)


def _classify_tier(credit_score: int) -> dict[str, Any]:
    """Return the credit tier config for a given score."""
    for tier in _TIER_THRESHOLDS:
        if credit_score >= tier["min_score"]:
            return tier
    return _CREDIT_TIERS["deep_subprime"]


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    logger.info(
        "rules_engine_started",
        tier_count=len(_CREDIT_TIERS),
    )
    yield
    logger.info("rules_engine_shutdown")


mcp = FastMCP(
    name="simulated-rules-engine",
    instructions=(
        "Simulated Rules Engine for SmartLedger. Evaluates loan/lease eligibility "
        "based on credit score tiers, LTV ratios, debt-to-income, vehicle age, "
        "and term limits. Call evaluate_eligibility before pricing."
    ),
    lifespan=lifespan,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_nested(data: dict, dotted_path: str) -> Any:
    """Get value from nested dict using dot notation."""
    keys = dotted_path.split(".")
    val: Any = data
    for key in keys:
        if not isinstance(val, dict):
            return None
        val = val.get(key)
    return val


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def evaluate_eligibility(application: dict) -> dict:
    """
    Evaluate a loan/lease application against all eligibility rules.

    Input:
      {
        "contract_type": "loan" | "lease",
        "credit_score": 725,
        "amount_financed": 28500.00,
        "vehicle_value": 31500.00,       # MSRP or appraised value
        "term_months": 72,
        "down_payment": 3000.00,
        "monthly_income": 6500.00,       # gross monthly income
        "existing_monthly_debt": 1200.00, # existing debt payments
        "vehicle_year": 2024
      }

    Returns:
      {
        "eligible": true|false,
        "credit_tier": "prime",
        "rules_evaluated": 6,
        "rules_passed": 6,
        "rules_failed": 0,
        "results": [
          {"rule": "credit_tier_eligible", "passed": true, ...},
          ...
        ],
        "warnings": [...]
      }
    """
    contract_type = application.get("contract_type", "loan")
    credit_score = int(application.get("credit_score", 0))
    amount_financed = float(application.get("amount_financed", 0))
    vehicle_value = float(application.get("vehicle_value", 0))
    term_months = int(application.get("term_months", 0))
    down_payment = float(application.get("down_payment", 0))
    monthly_income = float(application.get("monthly_income", 0))
    existing_monthly_debt = float(application.get("existing_monthly_debt", 0))
    vehicle_year = int(application.get("vehicle_year", date.today().year))

    tier = _classify_tier(credit_score)
    tier_name = tier["tier"]

    results: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    # ── Rule 1: Credit score minimum (300) ────────────────────────────────────
    rule_1_pass = credit_score >= 300
    results.append({
        "rule": "credit_score_minimum",
        "passed": rule_1_pass,
        "message": f"Credit score {credit_score} {'meets' if rule_1_pass else 'below'} minimum 300",
        "actual": credit_score,
        "threshold": 300,
    })

    # ── Rule 2: Lease eligibility by tier ─────────────────────────────────────
    if contract_type == "lease":
        max_lease_term = tier["max_term_lease"]
        rule_2_pass = max_lease_term > 0
        results.append({
            "rule": "lease_tier_eligible",
            "passed": rule_2_pass,
            "message": (
                f"Tier '{tier_name}' {'allows' if rule_2_pass else 'does not allow'} leases"
            ),
            "actual": tier_name,
            "threshold": "lease allowed",
        })
    else:
        rule_2_pass = True  # loans always available

    # ── Rule 3: LTV ratio ────────────────────────────────────────────────────
    if vehicle_value > 0:
        ltv = amount_financed / vehicle_value
        max_ltv = tier[f"max_ltv_{contract_type}"]
        rule_3_pass = ltv <= max_ltv
        results.append({
            "rule": "ltv_ratio",
            "passed": rule_3_pass,
            "message": (
                f"LTV {ltv:.1%} {'within' if rule_3_pass else 'exceeds'} "
                f"tier max {max_ltv:.0%}"
            ),
            "actual": round(ltv, 4),
            "threshold": max_ltv,
        })
    else:
        rule_3_pass = False
        results.append({
            "rule": "ltv_ratio",
            "passed": False,
            "message": "Vehicle value is required to calculate LTV",
            "actual": vehicle_value,
            "threshold": "> 0",
        })

    # ── Rule 4: Term limits ──────────────────────────────────────────────────
    max_term = tier[f"max_term_{contract_type}"]
    rule_4_pass = 0 < term_months <= max_term
    results.append({
        "rule": "term_limit",
        "passed": rule_4_pass,
        "message": (
            f"Term {term_months}mo {'within' if rule_4_pass else 'exceeds'} "
            f"tier max {max_term}mo"
        ),
        "actual": term_months,
        "threshold": max_term,
    })

    # ── Rule 5: Debt-to-income ratio ─────────────────────────────────────────
    if monthly_income > 0:
        # Estimate new payment for DTI check (rough: amount / term)
        estimated_payment = amount_financed / max(term_months, 1)
        total_monthly_debt = existing_monthly_debt + estimated_payment
        dti = total_monthly_debt / monthly_income
        max_dti = tier["max_dti"]
        rule_5_pass = dti <= max_dti
        results.append({
            "rule": "debt_to_income",
            "passed": rule_5_pass,
            "message": (
                f"DTI {dti:.1%} {'within' if rule_5_pass else 'exceeds'} "
                f"tier max {max_dti:.0%}"
            ),
            "actual": round(dti, 4),
            "threshold": max_dti,
        })
    else:
        rule_5_pass = True
        warnings.append({
            "code": "MISSING_INCOME",
            "message": "Monthly income not provided — DTI check skipped",
        })

    # ── Rule 6: Vehicle age ──────────────────────────────────────────────────
    vehicle_age = date.today().year - vehicle_year
    max_age = tier["max_vehicle_age_years"]
    rule_6_pass = vehicle_age <= max_age
    results.append({
        "rule": "vehicle_age",
        "passed": rule_6_pass,
        "message": (
            f"Vehicle age {vehicle_age}yr {'within' if rule_6_pass else 'exceeds'} "
            f"tier max {max_age}yr"
        ),
        "actual": vehicle_age,
        "threshold": max_age,
    })

    # ── Rule 7: Minimum down payment ─────────────────────────────────────────
    min_down_pct = tier["min_down_payment_pct"]
    if vehicle_value > 0 and min_down_pct > 0:
        min_down_amt = vehicle_value * min_down_pct
        rule_7_pass = down_payment >= min_down_amt
        results.append({
            "rule": "minimum_down_payment",
            "passed": rule_7_pass,
            "message": (
                f"Down payment ${down_payment:,.0f} "
                f"{'meets' if rule_7_pass else 'below'} "
                f"tier minimum ${min_down_amt:,.0f} ({min_down_pct:.0%})"
            ),
            "actual": down_payment,
            "threshold": min_down_amt,
        })
    else:
        rule_7_pass = True  # no minimum required

    # ── Aggregate ─────────────────────────────────────────────────────────────
    all_passed = all(r["passed"] for r in results)
    passed_count = sum(1 for r in results if r["passed"])
    failed_count = len(results) - passed_count

    logger.info(
        "eligibility_evaluated",
        credit_score=credit_score,
        tier=tier_name,
        contract_type=contract_type,
        eligible=all_passed,
        passed=passed_count,
        failed=failed_count,
    )

    return {
        "eligible": all_passed,
        "credit_tier": tier_name,
        "tier_config": tier,
        "rules_evaluated": len(results),
        "rules_passed": passed_count,
        "rules_failed": failed_count,
        "results": results,
        "warnings": warnings,
    }


@mcp.tool()
async def get_credit_tier(credit_score: int) -> dict:
    """
    Classify a credit score into a tier and return the tier configuration.

    Returns: {tier, min_score, max_score, description, max_ltv_loan, max_ltv_lease,
              max_term_loan, max_term_lease, max_dti, max_vehicle_age_years,
              min_down_payment_pct}
    """
    tier = _classify_tier(credit_score)
    logger.info("credit_tier_classified", credit_score=credit_score, tier=tier["tier"])
    return tier


@mcp.tool()
async def get_rule_set(contract_type: str = "loan") -> dict:
    """
    Return the full rule set for a contract type (loan or lease).
    Includes all tier configs and the rules that will be evaluated.

    Returns: {contract_type, tiers: {...}, rules: [...]}
    """
    rules = [
        {
            "rule": "credit_score_minimum",
            "description": "Credit score must be at least 300",
            "applies_to": ["loan", "lease"],
        },
        {
            "rule": "lease_tier_eligible",
            "description": "Deep subprime borrowers are not eligible for leases",
            "applies_to": ["lease"],
        },
        {
            "rule": "ltv_ratio",
            "description": "Loan-to-value ratio must not exceed tier maximum",
            "applies_to": ["loan", "lease"],
        },
        {
            "rule": "term_limit",
            "description": "Loan/lease term must not exceed tier maximum",
            "applies_to": ["loan", "lease"],
        },
        {
            "rule": "debt_to_income",
            "description": "Debt-to-income ratio must not exceed tier maximum",
            "applies_to": ["loan", "lease"],
        },
        {
            "rule": "vehicle_age",
            "description": "Vehicle age must not exceed tier maximum",
            "applies_to": ["loan", "lease"],
        },
        {
            "rule": "minimum_down_payment",
            "description": "Down payment must meet tier minimum percentage",
            "applies_to": ["loan", "lease"],
        },
    ]

    applicable_rules = [r for r in rules if contract_type in r["applies_to"]]

    return {
        "contract_type": contract_type,
        "tiers": _CREDIT_TIERS,
        "rules": applicable_rules,
        "rule_count": len(applicable_rules),
    }


@mcp.tool()
async def list_rule_sets() -> list[dict]:
    """Return available rule set names."""
    return [
        {"name": "loan", "description": "Auto loan eligibility rules", "rule_count": 7},
        {"name": "lease", "description": "Auto lease eligibility rules", "rule_count": 7},
    ]


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8020)
