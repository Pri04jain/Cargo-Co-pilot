"""
Document routes: upload, list, get status.

Upload flow:
1. Validate file type and size
2. Save file to UPLOAD_DIR with a UUID-based path (prevents filename collisions)
3. Create Document record in DB with status=queued
4. Enqueue Celery task for background processing
5. Return immediately with document_id and task_id

This keeps the HTTP response fast regardless of document size or pipeline complexity.
"""

import os
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, HTTPException, UploadFile, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DB
from app.core.config import settings
from app.core.logging import get_logger
from app.models.audit import AuditLog
from app.models.document import Document, DocumentStatus
from app.schemas.document import DocumentListOut, DocumentOut, UploadResponse
from app.workers.tasks import process_document

logger = get_logger(__name__)

router = APIRouter()

# MIME type to extension mapping for validation
ALLOWED_MIME_TYPES = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/tiff": "tiff",
}


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile,
    current_user: CurrentUser,
    db: DB,
) -> UploadResponse:
    """
    Upload a shipping document for processing.
    Returns 202 Accepted immediately; processing happens in the background.
    """
    # ── Validation ────────────────────────────────────────────────────────────
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File type '{file.content_type}' not supported. "
                   f"Allowed: {', '.join(ALLOWED_MIME_TYPES.keys())}",
        )

    # Read file into memory to check size before saving
    # For very large files in production you'd stream directly to storage
    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {settings.max_upload_size_mb}MB",
        )

    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File is empty",
        )

    # ── Store file ────────────────────────────────────────────────────────────
    # Use UUID as filename to prevent path traversal and collisions
    doc_id = uuid.uuid4()
    ext = ALLOWED_MIME_TYPES[file.content_type]
    storage_filename = f"{doc_id}.{ext}"

    # Partition by tenant for easier management
    tenant_upload_dir = Path(settings.upload_dir) / str(current_user.tenant_id)
    tenant_upload_dir.mkdir(parents=True, exist_ok=True)

    storage_path = tenant_upload_dir / storage_filename

    async with aiofiles.open(storage_path, "wb") as f:
        await f.write(content)

    logger.info(
        "document_stored",
        document_id=str(doc_id),
        filename=file.filename,
        size_bytes=len(content),
        path=str(storage_path),
    )

    # ── Create DB record ──────────────────────────────────────────────────────
    document = Document(
        id=doc_id,
        tenant_id=current_user.tenant_id,
        uploaded_by=current_user.id,
        original_filename=file.filename or storage_filename,
        storage_path=str(storage_path),
        file_size_bytes=len(content),
        mime_type=file.content_type,
        status=DocumentStatus.QUEUED,
    )
    db.add(document)

    # Write initial audit log entry
    audit = AuditLog(
        document_id=doc_id,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        actor=current_user.email,
        event_type="document_uploaded",
        description=f"Uploaded '{file.filename}'",
        metadata={
            "filename": file.filename,
            "size_bytes": len(content),
            "mime_type": file.content_type,
        },
    )
    db.add(audit)
    await db.flush()

    # ── Enqueue background task ───────────────────────────────────────────────
    task = process_document.delay(
        document_id=str(doc_id),
        tenant_id=str(current_user.tenant_id),
        storage_path=str(storage_path),
    )

    # Save task ID so the client can poll status
    document.celery_task_id = task.id
    await db.flush()

    logger.info(
        "document_queued",
        document_id=str(doc_id),
        task_id=task.id,
    )

    return UploadResponse(
        document_id=doc_id,
        status=DocumentStatus.QUEUED,
        task_id=task.id,
        message="Document uploaded and queued for processing",
    )


@router.get("", response_model=DocumentListOut)
async def list_documents(
    current_user: CurrentUser,
    db: DB,
    page: int = 1,
    page_size: int = 20,
    status_filter: DocumentStatus | None = None,
) -> DocumentListOut:
    """
    List documents for the current tenant.
    All queries are scoped by tenant_id — users from different tenants
    cannot see each other's documents.
    """
    page_size = min(page_size, 100)  # cap to prevent large queries
    offset = (page - 1) * page_size

    query = select(Document).where(Document.tenant_id == current_user.tenant_id)
    count_query = select(func.count()).select_from(Document).where(
        Document.tenant_id == current_user.tenant_id
    )

    if status_filter:
        query = query.where(Document.status == status_filter)
        count_query = count_query.where(Document.status == status_filter)

    query = query.order_by(Document.created_at.desc()).offset(offset).limit(page_size)

    results = await db.execute(query)
    documents = results.scalars().all()

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    return DocumentListOut(
        items=list(documents),
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
) -> Document:
    """Get a single document, scoped to the current tenant."""
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == current_user.tenant_id,  # tenant isolation
        )
    )
    document = result.scalar_one_or_none()

    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    return document


@router.get("/{document_id}/status")
async def get_document_status(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
) -> dict:
    """
    Lightweight status endpoint for polling.
    The frontend polls this after upload until status is no longer 'processing'.
    """
    result = await db.execute(
        select(Document.id, Document.status, Document.celery_task_id, Document.error_message).where(
            Document.id == document_id,
            Document.tenant_id == current_user.tenant_id,
        )
    )
    row = result.first()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    return {
        "document_id": str(row.id),
        "status": row.status,
        "task_id": row.celery_task_id,
        "error": row.error_message,
    }
