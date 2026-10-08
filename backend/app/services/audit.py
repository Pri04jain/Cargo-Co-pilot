"""
Audit log service.

Centralises the logic for writing audit entries so every pipeline node
and route handler uses a consistent interface instead of constructing
AuditLog objects directly.

All writes are fire-and-forget from the caller's perspective — the session
commit is the caller's responsibility (keeps transaction control explicit).
"""

import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.audit import AuditLog


async def log_event_async(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    document_id: Optional[uuid.UUID] = None,
    user_id: Optional[uuid.UUID] = None,
    actor: str = "system",
    description: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> AuditLog:
    """Write an audit log entry in an async context (FastAPI routes)."""
    entry = AuditLog(
        tenant_id=tenant_id,
        document_id=document_id,
        user_id=user_id,
        actor=actor,
        event_type=event_type,
        description=description,
        metadata=metadata,
    )
    db.add(entry)
    # Caller must await db.flush() or db.commit() to persist
    return entry


def log_event_sync(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    document_id: Optional[uuid.UUID] = None,
    user_id: Optional[uuid.UUID] = None,
    actor: str = "system",
    description: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> AuditLog:
    """Write an audit log entry in a sync context (Celery tasks)."""
    entry = AuditLog(
        tenant_id=tenant_id,
        document_id=document_id,
        user_id=user_id,
        actor=actor,
        event_type=event_type,
        description=description,
        metadata=metadata,
    )
    db.add(entry)
    return entry
