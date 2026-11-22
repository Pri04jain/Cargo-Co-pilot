"""
Declarative base and shared mixin columns.

Every model gets id, created_at, updated_at automatically.
Most models also get tenant_id via TenantMixin — this enforces
row-level multi-tenancy at the ORM layer.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    """Adds created_at / updated_at to any model."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TenantMixin:
    """
    Adds tenant_id to any model.
    Every query MUST filter by tenant_id — enforced in the service layer.
    We don't use Postgres Row-Level Security for simplicity, but tenant_id
    is indexed so the check is cheap.
    """
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
