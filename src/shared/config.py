"""
Shared configuration loaded from environment variables.
All services import from here — single source of truth for config.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ─── Phase & Mode ─────────────────────────────────────────────────────────
    phase: int = 0
    write_guard: bool = True
    log_level: str = "INFO"
    service_name: str = "smartledger"

    # ─── Infrastructure ───────────────────────────────────────────────────────
    database_url: str = "postgresql://smartledger:smartledger_dev@localhost:5432/smartledger"
    redis_url: str = "redis://localhost:6379"

    # ─── Anthropic ────────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-20241022"

    # ─── MCP Server URLs ──────────────────────────────────────────────────────
    mcp_validation_url: str = "http://localhost:8001"
    mcp_ledger_url: str = "http://localhost:8002"
    mcp_semantic_ai_url: str = "http://localhost:8003"
    mcp_reporting_url: str = "http://localhost:8004"

    # Simulated systems
    mcp_oracle_los_url: str = "http://localhost:8010"
    mcp_salesforce_los_url: str = "http://localhost:8011"
    mcp_llas_url: str = "http://localhost:8012"
    mcp_crm_url: str = "http://localhost:8013"
    mcp_payment_url: str = "http://localhost:8014"
    mcp_insurance_url: str = "http://localhost:8015"
    mcp_dealer_url: str = "http://localhost:8016"
    mcp_customer_portal_url: str = "http://localhost:8017"
    mcp_mobile_app_url: str = "http://localhost:8018"
    mcp_ivr_url: str = "http://localhost:8019"

    # ─── JWT (agent session tokens) ───────────────────────────────────────────
    jwt_secret: str = "change-me-in-production"
    jwt_expiry_seconds: int = 3600

    # ─── Validation Proof Tokens (single-use, 60s, Validation Engine → Ledger)
    proof_token_secret: str = "change-me-proof-token-secret"
    proof_token_expiry_seconds: int = 60

    # ─── Hyperledger Fabric ───────────────────────────────────────────────────
    fabric_peer_endpoint: str = ""
    fabric_channel: str = "smartledger-channel"
    fabric_chaincode: str = "smartledger-cc"
    fabric_msp_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
