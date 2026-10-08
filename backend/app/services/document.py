"""
Document service — shared query helpers used by routes and pipeline nodes.

Keeping DB queries in a service layer (rather than inline in routes) makes
them reusable and easier to unit test with a mocked session.
"""

import uuid
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus


async def get_document_async(
    db: AsyncSession,
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Optional[Document]:
    """Fetch a document, scoped to the tenant. Returns None if not found."""
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
        )
    )
    return result.scalar_one_or_none()


def get_document_sync(
    db: Session,
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Optional[Document]:
    result = db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
        )
    )
    return result.scalar_one_or_none()


def update_document_status_sync(
    db: Session,
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    new_status: DocumentStatus,
    error_message: Optional[str] = None,
) -> Optional[Document]:
    """Update status and optionally set an error message. Returns the updated doc."""
    doc = get_document_sync(db, document_id, tenant_id)
    if doc is None:
        return None
    doc.status = new_status
    if error_message is not None:
        doc.error_message = error_message[:2000]
    return doc
