"""
Pricing Engine Simulated MCP Server

Simulates an enterprise Pricing Engine that calculates interest rates and
monthly payments for auto loans and leases based on credit tier, term,
LTV, vehicle age, and dealer adjustments.

Rate Structure:
  Base rate by credit tier → adjusted for term, LTV, vehicle age.
  Dealer markup capped at 2.0% (regulatory / company policy).
  Lease uses money factor instead of APR (internally converted).

Tools:
  - calculate_rate(request)         → final APR/money factor + breakdown
  - calculate_payment(request)      → monthly payment + amortization summary
  - get_rate_card(contract_type?)   → full rate table for all tiers
  - get_pricing_factors()           → all adjustment factors
"""

import math
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from shared.config import get_settings
from shared.logging import configure_logging, get_logger

# ─── Init ─────────────────────────────────────────────────────────────────────

settings = get_settings()
configure_logging("pricing-engine", settings.log_level)
logger = get_logger(__name__)

# ─── Rate Tables ─────────────────────────────────────────────────────────────

# Base APR by credit tier and contract type
_BASE_RATES: dict[str, dict[str, float]] = {
    "super_prime":   {"loan": 4.49, "lease": 3.99},
    "prime":         {"loan": 5.99, "lease": 4.99},
    "near_prime":    {"loan": 8.49, "lease": 7.49},
    "subprime":      {"loan": 12.99, "lease": 11.99},
    "deep_subprime": {"loan": 18.99, "lease": 0.0},  # no lease
}

# Term adjustment: longer terms = higher rate
_TERM_ADJUSTMENTS: dict[str, float] = {
    "1-36":  -0.50,   # short term discount
    "37-48":  0.00,   # baseline
    "49-60":  0.25,
    "61-72":  0.75,
    "73-84":  1.25,   # longest term premium
}

# LTV adjustment: higher LTV = higher rate
_LTV_ADJUSTMENTS: dict[str, float] = {
    "0-80":    -0.25,  # low LTV discount
    "81-90":    0.00,  # baseline
    "91-100":   0.50,
    "101-110":  1.00,
    "111-120":  1.50,  # high LTV premium
}

# Vehicle age adjustment: older = higher rate
_VEHICLE_AGE_ADJUSTMENTS: dict[str, float] = {
    "0-1":  0.00,   # new / 1yr
    "2-3":  0.25,
    "4-5":  0.75,
    "6-7":  1.25,
    "8+":   2.00,
}

# Maximum dealer markup (regulatory cap)
_MAX_DEALER_MARKUP: float = 2.00  # percentage points


def _get_term_adjustment(term_months: int) -> tuple[str, float]:
    """Return (bucket_label, adjustment) for a term."""
    if term_months <= 36:
        return "1-36", _TERM_ADJUSTMENTS["1-36"]
    elif term_months <= 48:
        return "37-48", _TERM_ADJUSTMENTS["37-48"]
    elif term_months <= 60:
        return "49-60", _TERM_ADJUSTMENTS["49-60"]
    elif term_months <= 72:
        return "61-72", _TERM_ADJUSTMENTS["61-72"]
    else:
        return "73-84", _TERM_ADJUSTMENTS["73-84"]


def _get_ltv_adjustment(ltv_pct: float) -> tuple[str, float]:
    """Return (bucket_label, adjustment) for an LTV ratio (as percentage 0-120+)."""
    if ltv_pct <= 80:
        return "0-80", _LTV_ADJUSTMENTS["0-80"]
    elif ltv_pct <= 90:
        return "81-90", _LTV_ADJUSTMENTS["81-90"]
    elif ltv_pct <= 100:
        return "91-100", _LTV_ADJUSTMENTS["91-100"]
    elif ltv_pct <= 110:
        return "101-110", _LTV_ADJUSTMENTS["101-110"]
    else:
        return "111-120", _LTV_ADJUSTMENTS["111-120"]


def _get_vehicle_age_adjustment(vehicle_age: int) -> tuple[str, float]:
    """Return (bucket_label, adjustment) for vehicle age in years."""
    if vehicle_age <= 1:
        return "0-1", _VEHICLE_AGE_ADJUSTMENTS["0-1"]
    elif vehicle_age <= 3:
        return "2-3", _VEHICLE_AGE_ADJUSTMENTS["2-3"]
    elif vehicle_age <= 5:
        return "4-5", _VEHICLE_AGE_ADJUSTMENTS["4-5"]
    elif vehicle_age <= 7:
        return "6-7", _VEHICLE_AGE_ADJUSTMENTS["6-7"]
    else:
        return "8+", _VEHICLE_AGE_ADJUSTMENTS["8+"]


def _calculate_monthly_payment(
    principal: float,
    annual_rate: float,
    term_months: int,
) -> float:
    """Standard amortization formula: M = P * [r(1+r)^n] / [(1+r)^n - 1]."""
    if annual_rate <= 0:
        return round(principal / term_months, 2)
    r = annual_rate / 100 / 12  # monthly rate
    n = term_months
    payment = principal * (r * math.pow(1 + r, n)) / (math.pow(1 + r, n) - 1)
    return round(payment, 2)


def _calculate_lease_payment(
    vehicle_value: float,
    residual_value: float,
    money_factor: float,
    term_months: int,
    down_payment: float = 0,
) -> float:
    """
    Standard lease payment formula:
      depreciation = (adjusted_cap_cost - residual) / term
      finance_charge = (adjusted_cap_cost + residual) * money_factor
      payment = depreciation + finance_charge
    """
    adjusted_cap_cost = vehicle_value - down_payment
    depreciation = (adjusted_cap_cost - residual_value) / term_months
    finance_charge = (adjusted_cap_cost + residual_value) * money_factor
    return round(depreciation + finance_charge, 2)


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    logger.info(
        "pricing_engine_started",
        tier_count=len(_BASE_RATES),
    )
    yield
    logger.info("pricing_engine_shutdown")


mcp = FastMCP(
    name="simulated-pricing-engine",
    instructions=(
        "Simulated Pricing Engine for SmartLedger. Calculates interest rates "
        "and monthly payments for auto loans and leases. Uses credit-tier-based "
        "rate cards with adjustments for term, LTV, vehicle age, and dealer markup."
    ),
    lifespan=lifespan,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def calculate_rate(request: dict) -> dict:
    """
    Calculate the final interest rate (APR) for a loan/lease application.

    Input:
      {
        "contract_type": "loan" | "lease",
        "credit_tier": "prime",
        "term_months": 72,
        "amount_financed": 28500.00,
        "vehicle_value": 31500.00,
        "vehicle_year": 2024,
        "dealer_markup": 0.50       # optional, max 2.0%
      }

    Returns:
      {
        "final_rate": 6.74,
        "base_rate": 5.99,
        "adjustments": [...],
        "dealer_markup_applied": 0.50,
        "credit_tier": "prime",
        "rate_type": "APR" | "money_factor"
      }
    """
    contract_type = request.get("contract_type", "loan")
    credit_tier = request.get("credit_tier", "prime")
    term_months = int(request.get("term_months", 60))
    amount_financed = float(request.get("amount_financed", 0))
    vehicle_value = float(request.get("vehicle_value", 0))
    vehicle_year = int(request.get("vehicle_year", date.today().year))
    dealer_markup = min(float(request.get("dealer_markup", 0)), _MAX_DEALER_MARKUP)

    # Base rate
    tier_rates = _BASE_RATES.get(credit_tier, _BASE_RATES["subprime"])
    base_rate = tier_rates.get(contract_type, tier_rates["loan"])

    if contract_type == "lease" and base_rate == 0:
        return {
            "error": f"Lease not available for credit tier '{credit_tier}'",
            "eligible": False,
        }

    adjustments: list[dict[str, Any]] = []

    # Term adjustment
    term_bucket, term_adj = _get_term_adjustment(term_months)
    adjustments.append({
        "factor": "term",
        "bucket": term_bucket,
        "adjustment": term_adj,
        "reason": f"Term {term_months}mo in bucket {term_bucket}",
    })

    # LTV adjustment
    ltv_pct = (amount_financed / vehicle_value * 100) if vehicle_value > 0 else 90
    ltv_bucket, ltv_adj = _get_ltv_adjustment(ltv_pct)
    adjustments.append({
        "factor": "ltv",
        "bucket": ltv_bucket,
        "adjustment": ltv_adj,
        "reason": f"LTV {ltv_pct:.1f}% in bucket {ltv_bucket}",
    })

    # Vehicle age adjustment
    vehicle_age = date.today().year - vehicle_year
    age_bucket, age_adj = _get_vehicle_age_adjustment(vehicle_age)
    adjustments.append({
        "factor": "vehicle_age",
        "bucket": age_bucket,
        "adjustment": age_adj,
        "reason": f"Vehicle age {vehicle_age}yr in bucket {age_bucket}",
    })

    # Dealer markup
    if dealer_markup > 0:
        adjustments.append({
            "factor": "dealer_markup",
            "bucket": "n/a",
            "adjustment": dealer_markup,
            "reason": f"Dealer markup {dealer_markup:.2f}% (max {_MAX_DEALER_MARKUP:.1f}%)",
        })

    # Sum adjustments
    total_adjustment = sum(a["adjustment"] for a in adjustments)
    final_rate = round(max(base_rate + total_adjustment, 0.0), 2)

    # For leases, also provide money factor (APR / 2400)
    money_factor = round(final_rate / 2400, 6) if contract_type == "lease" else None

    logger.info(
        "rate_calculated",
        credit_tier=credit_tier,
        contract_type=contract_type,
        base_rate=base_rate,
        final_rate=final_rate,
        total_adjustment=total_adjustment,
    )

    return {
        "final_rate": final_rate,
        "base_rate": base_rate,
        "total_adjustment": round(total_adjustment, 2),
        "adjustments": adjustments,
        "dealer_markup_applied": dealer_markup,
        "dealer_markup_max": _MAX_DEALER_MARKUP,
        "credit_tier": credit_tier,
        "contract_type": contract_type,
        "rate_type": "money_factor" if contract_type == "lease" else "APR",
        "money_factor": money_factor,
    }


@mcp.tool()
async def calculate_payment(request: dict) -> dict:
    """
    Calculate the monthly payment for a loan or lease.

    Input:
      {
        "contract_type": "loan" | "lease",
        "amount_financed": 28500.00,
        "vehicle_value": 31500.00,
        "residual_value": 13000.00,   # lease only
        "annual_rate": 6.74,          # APR (use calculate_rate to get this)
        "term_months": 72,
        "down_payment": 3000.00
      }

    Returns:
      {
        "monthly_payment": 487.23,
        "total_of_payments": 35080.56,
        "total_interest": 6580.56,
        "total_cost": 38080.56,
        "effective_principal": 28500.00
      }
    """
    contract_type = request.get("contract_type", "loan")
    amount_financed = float(request.get("amount_financed", 0))
    vehicle_value = float(request.get("vehicle_value", amount_financed))
    residual_value = float(request.get("residual_value", 0))
    annual_rate = float(request.get("annual_rate", 0))
    term_months = int(request.get("term_months", 60))
    down_payment = float(request.get("down_payment", 0))

    if contract_type == "lease":
        money_factor = annual_rate / 2400
        monthly_payment = _calculate_lease_payment(
            vehicle_value=vehicle_value,
            residual_value=residual_value,
            money_factor=money_factor,
            term_months=term_months,
            down_payment=down_payment,
        )
        total_of_payments = round(monthly_payment * term_months, 2)
        # For lease: "interest" = total payments + residual + down - vehicle value
        total_interest = round(total_of_payments + residual_value + down_payment - vehicle_value, 2)
        total_cost = round(total_of_payments + down_payment, 2)

        logger.info(
            "lease_payment_calculated",
            monthly_payment=monthly_payment,
            money_factor=money_factor,
            term_months=term_months,
        )

        return {
            "monthly_payment": monthly_payment,
            "total_of_payments": total_of_payments,
            "total_interest": max(total_interest, 0),
            "total_cost": total_cost,
            "effective_principal": round(vehicle_value - down_payment, 2),
            "residual_value": residual_value,
            "money_factor": round(money_factor, 6),
            "contract_type": "lease",
            "annual_rate": annual_rate,
            "term_months": term_months,
        }

    # Loan calculation
    monthly_payment = _calculate_monthly_payment(
        principal=amount_financed,
        annual_rate=annual_rate,
        term_months=term_months,
    )
    total_of_payments = round(monthly_payment * term_months, 2)
    total_interest = round(total_of_payments - amount_financed, 2)
    total_cost = round(total_of_payments + down_payment, 2)

    logger.info(
        "loan_payment_calculated",
        monthly_payment=monthly_payment,
        annual_rate=annual_rate,
        term_months=term_months,
    )

    return {
        "monthly_payment": monthly_payment,
        "total_of_payments": total_of_payments,
        "total_interest": total_interest,
        "total_cost": total_cost,
        "effective_principal": amount_financed,
        "contract_type": "loan",
        "annual_rate": annual_rate,
        "term_months": term_months,
    }


@mcp.tool()
async def get_rate_card(contract_type: str = "loan") -> dict:
    """
    Return the full rate card showing base rates for all tiers.
    Includes adjustment tables for reference.

    Returns: {contract_type, rates: {tier: base_rate}, adjustments: {...}}
    """
    rates = {}
    for tier, tier_rates in _BASE_RATES.items():
        rate = tier_rates.get(contract_type, 0)
        rates[tier] = {
            "base_rate": rate,
            "available": rate > 0,
        }

    return {
        "contract_type": contract_type,
        "rates": rates,
        "term_adjustments": _TERM_ADJUSTMENTS,
        "ltv_adjustments": _LTV_ADJUSTMENTS,
        "vehicle_age_adjustments": _VEHICLE_AGE_ADJUSTMENTS,
        "max_dealer_markup": _MAX_DEALER_MARKUP,
    }


@mcp.tool()
async def get_pricing_factors() -> dict:
    """Return all pricing adjustment factors and caps."""
    return {
        "base_rates": _BASE_RATES,
        "term_adjustments": _TERM_ADJUSTMENTS,
        "ltv_adjustments": _LTV_ADJUSTMENTS,
        "vehicle_age_adjustments": _VEHICLE_AGE_ADJUSTMENTS,
        "max_dealer_markup": _MAX_DEALER_MARKUP,
        "rate_floor": 0.0,
        "rate_cap": 36.0,  # matches validation engine RULE-BIZ-RATE
    }


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8021)
