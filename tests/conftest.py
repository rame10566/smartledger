"""
Global test configuration for SmartLedger.

Adds src/ to sys.path so MCP server modules are importable as:
  from mcp_servers.simulated.oracle_los.server import ...
  from mcp_servers.validation.server import ...
  from mcp_servers.ledger.server import ...
  from shared.config import get_settings
"""
import sys
import os

# Add src/ to path so all packages are importable
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

# Set test environment variables before any imports
os.environ.setdefault("PROOF_TOKEN_SECRET", "test-proof-token-secret-32chars-long!")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("DATABASE_URL", "postgresql://smartledger:smartledger_dev@localhost:5432/smartledger")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("WRITE_GUARD", "true")
os.environ.setdefault("PHASE", "0")
os.environ.setdefault("LOG_LEVEL", "WARNING")    # suppress logs in tests

# Configure structlog for tests: minimal processors that work with PrintLogger.
# The default add_logger_name processor fails in test context (PrintLogger has no .name).
import structlog
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer(colors=False),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(30),  # WARNING level
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=False,
)
