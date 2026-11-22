"""
Structured logging setup using structlog.
Outputs JSON in production (easy to ship to any log aggregator),
pretty-prints in development (easy to read in the terminal).

Why structlog over the stdlib logging module?
- Automatic key=value context binding (user_id, tenant_id, doc_id, etc.)
- Processors chain makes it easy to add/remove fields without touching call sites.
"""

import logging
import sys

import structlog

from app.core.config import settings


def configure_logging() -> None:
    """Call once at app startup to configure structlog and stdlib logging."""

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,           # picks up bound context (tenant_id, etc.)
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.environment == "development":
        # Human-readable output for local dev
        renderer = structlog.dev.ConsoleRenderer()
    else:
        # JSON output for production log shipping
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(settings.log_level.upper())


def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
