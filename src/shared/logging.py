"""
Structured JSON logging — all services use this.
Outputs logs compatible with ELK/Loki ingestion.
"""
import logging
import sys
from typing import Any

import structlog


def configure_logging(service_name: str, log_level: str = "INFO") -> None:
    """Configure structured JSON logging for a service."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Bind service name to all log entries
    structlog.contextvars.bind_contextvars(service=service_name)


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
