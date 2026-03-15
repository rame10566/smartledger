"""
SmartLedger Governance Dashboard API

FastAPI REST server that serves the Governance Dashboard frontend.
Reads from PostgreSQL directly and calls Ledger / Validation MCP servers.

Endpoints:
  GET  /api/health
  GET  /api/quarantine             — list pending quarantined events
  GET  /api/quarantine/{event_id}  — single quarantine record
  POST /api/quarantine/{event_id}/approve  — human approves override
  POST /api/quarantine/{event_id}/reject   — human rejects
  GET  /api/contracts/{contract_id}/lifecycle
  GET  /api/contracts/{contract_id}/audit
  GET  /api/contracts/{contract_id}/state
  GET  /api/contracts              — list contracts (recent)

CORS is open for POC (Dashboard UI is on :3000, API on :8000).
"""

from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.config import get_settings
from shared.logging import configure_logging, get_logger

from dashboard_api.routers import contracts, quarantine, reports

settings = get_settings()
configure_logging(service_name="dashboard-api", log_level=settings.log_level)
log = get_logger(__name__)

# Module-level pool shared across requests via app.state
_pool: asyncpg.Pool | None = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised")
    return _pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    log.info("dashboard_api_starting")

    _pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    app.state.pool = _pool
    log.info("dashboard_api_ready")

    yield

    log.info("dashboard_api_stopping")
    await _pool.close()
    log.info("dashboard_api_stopped")


app = FastAPI(
    title="SmartLedger Dashboard API",
    version="0.1.0",
    description="Governance Dashboard REST API for the SmartLedger POC",
    lifespan=lifespan,
)

# Allow the Next.js dev server and production build to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restrict in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(quarantine.router, prefix="/api")
app.include_router(contracts.router,  prefix="/api")
app.include_router(reports.router,    prefix="/api")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    pool = app.state.pool
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception as e:
        db_ok = False
        log.error("health_check_db_failed", error=str(e))

    return {
        "status":   "ok" if db_ok else "degraded",
        "db":       "ok" if db_ok else "error",
        "service":  "dashboard-api",
        "version":  "0.1.0",
        "phase":    settings.phase,
        "write_guard": settings.write_guard,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("dashboard_api.main:app", host="0.0.0.0", port=8000, reload=True)
