"""
Document routes: upload, batch_upload, list, detail, review actions.
"""

import os
import uuid
from pathlib import Path
from typing import List

import aiofiles
from fastapi import APIRouter, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DB
from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import rate_limit
from app.models.audit import AuditLog
from app.models.document import Document, DocumentStatus
from app.models.extraction import Extraction, ExtractedField
from app.models.review import ReviewAction, ReviewActionType
from app.models.routing import RoutingDecision
from app.models.validation import ValidationResult
from app.schemas.document import DocumentListOut, DocumentOut, UploadResponse
from app.workers.tasks import process_document

logger = get_logger(__name__)
router = APIRouter()

ALLOWED_MIME_TYPES = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/tiff": "tiff",
}


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(file: UploadFile, request: Request, current_user: CurrentUser, db: DB) -> UploadResponse:
    await rate_limit(request, "upload", tenant_id=str(current_user.tenant_id))

    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File type '{file.content_type}' not supported.",
        )
    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"File exceeds {settings.max_upload_size_mb}MB")
    if len(content) == 0:
        raise HTTPException(status_code=422, detail="File is empty")

    doc_id = uuid.uuid4()
    ext = ALLOWED_MIME_TYPES[file.content_type]
    tenant_dir = Path(settings.upload_dir) / str(current_user.tenant_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)
    storage_path = tenant_dir / f"{doc_id}.{ext}"

    async with aiofiles.open(storage_path, "wb") as f:
        await f.write(content)

    document = Document(
        id=doc_id, tenant_id=current_user.tenant_id, uploaded_by=current_user.id,
        original_filename=file.filename or f"{doc_id}.{ext}",
        storage_path=str(storage_path), file_size_bytes=len(content),
        mime_type=file.content_type, status=DocumentStatus.QUEUED,
    )
    db.add(document)
    db.add(AuditLog(
        document_id=doc_id, tenant_id=current_user.tenant_id,
        user_id=current_user.id, actor=current_user.email,
        event_type="document_uploaded",
        description=f"Uploaded '{file.filename}'",
        event_metadata={"filename": file.filename, "size_bytes": len(content)},
    ))
    await db.flush()

    task = process_document.delay(
        document_id=str(doc_id),
        tenant_id=str(current_user.tenant_id),
        storage_path=str(storage_path),
    )
    document.celery_task_id = task.id
    await db.flush()

    return UploadResponse(document_id=doc_id, status=DocumentStatus.QUEUED,
                          task_id=task.id, message="Document queued for processing")


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=DocumentListOut)
async def list_documents(
    current_user: CurrentUser, db: DB,
    page: int = 1, page_size: int = 20,
    status_filter: DocumentStatus | None = None,
) -> DocumentListOut:
    page_size = min(page_size, 100)
    offset = (page - 1) * page_size
    q = select(Document).where(Document.tenant_id == current_user.tenant_id)
    cq = select(func.count()).select_from(Document).where(Document.tenant_id == current_user.tenant_id)
    if status_filter:
        q = q.where(Document.status == status_filter)
        cq = cq.where(Document.status == status_filter)
    q = q.order_by(Document.created_at.desc()).offset(offset).limit(page_size)
    docs = (await db.execute(q)).scalars().all()
    total = (await db.execute(cq)).scalar_one()
    return DocumentListOut(items=list(docs), total=total, page=page, page_size=page_size)


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(document_id: uuid.UUID, current_user: CurrentUser, db: DB) -> Document:
    doc = await _get_doc_or_404(db, document_id, current_user.tenant_id)
    return doc


@router.get("/{document_id}/status")
async def get_document_status(document_id: uuid.UUID, current_user: CurrentUser, db: DB) -> dict:
    row = (await db.execute(
        select(Document.id, Document.status, Document.celery_task_id, Document.error_message)
        .where(Document.id == document_id, Document.tenant_id == current_user.tenant_id)
    )).first()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"document_id": str(row.id), "status": row.status,
            "task_id": row.celery_task_id, "error": row.error_message}


@router.get("/{document_id}/fields")
async def get_document_fields(document_id: uuid.UUID, current_user: CurrentUser, db: DB) -> dict:
    """All extracted fields for a document (current extraction only)."""
    await _get_doc_or_404(db, document_id, current_user.tenant_id)

    # Get current extraction
    extraction = (await db.execute(
        select(Extraction).where(
            Extraction.document_id == document_id,
            Extraction.tenant_id == current_user.tenant_id,
            Extraction.is_current == True,  # noqa: E712
        )
    )).scalar_one_or_none()

    if not extraction:
        return {"fields": [], "extraction": None}

    fields = (await db.execute(
        select(ExtractedField).where(
            ExtractedField.extraction_id == extraction.id,
            ExtractedField.tenant_id == current_user.tenant_id,
        ).order_by(ExtractedField.field_name)
    )).scalars().all()

    return {
        "extraction": {
            "id": str(extraction.id),
            "prompt_version": extraction.prompt_version,
            "model": extraction.model,
            "attempt": extraction.attempt,
            "created_at": extraction.created_at.isoformat(),
        },
        "fields": [
            {
                "id": str(f.id),
                "field_name": f.field_name,
                "value": f.value,
                "normalized_value": f.normalized_value,
                "confidence": f.confidence,
                "evidence": f.evidence,
                "status": f.status,
            }
            for f in fields
        ],
    }


@router.get("/{document_id}/validation")
async def get_validation_results(document_id: uuid.UUID, current_user: CurrentUser, db: DB) -> dict:
    await _get_doc_or_404(db, document_id, current_user.tenant_id)
    results = (await db.execute(
        select(ValidationResult).where(
            ValidationResult.document_id == document_id,
            ValidationResult.tenant_id == current_user.tenant_id,
        )
    )).scalars().all()
    return {
        "results": [
            {
                "id": str(r.id),
                "check_name": r.check_name,
                "status": r.status,
                "severity": r.severity,
                "message": r.message,
                "field_name": r.field_name,
            }
            for r in results
        ],
        "error_count": sum(1 for r in results if r.status == "fail" and r.severity == "error"),
        "warning_count": sum(1 for r in results if r.status == "fail" and r.severity == "warning"),
    }


@router.get("/{document_id}/routing")
async def get_routing_decision(document_id: uuid.UUID, current_user: CurrentUser, db: DB) -> dict:
    await _get_doc_or_404(db, document_id, current_user.tenant_id)
    decision = (await db.execute(
        select(RoutingDecision).where(
            RoutingDecision.document_id == document_id,
            RoutingDecision.tenant_id == current_user.tenant_id,
        ).order_by(RoutingDecision.created_at.desc())
    )).scalar_one_or_none()

    if not decision:
        return {"decision": None}
    return {
        "id": str(decision.id),
        "decision": decision.decision,
        "rule_id": decision.rule_id,
        "reason": decision.reason,
        "rules_version": decision.rules_version,
        "evaluation_context": decision.evaluation_context,
        "created_at": decision.created_at.isoformat(),
    }


@router.get("/{document_id}/audit")
async def get_audit_log(document_id: uuid.UUID, current_user: CurrentUser, db: DB) -> dict:
    await _get_doc_or_404(db, document_id, current_user.tenant_id)
    entries = (await db.execute(
        select(AuditLog).where(
            AuditLog.document_id == document_id,
            AuditLog.tenant_id == current_user.tenant_id,
        ).order_by(AuditLog.created_at.asc())
    )).scalars().all()
    return {
        "entries": [
            {
                "id": str(e.id),
                "actor": e.actor,
                "event_type": e.event_type,
                "description": e.description,
                "metadata": e.event_metadata,
                "created_at": e.created_at.isoformat(),
            }
            for e in entries
        ]
    }


# ── Review actions ────────────────────────────────────────────────────────────

class ApproveRequest(BaseModel):
    comment: str | None = None


class RejectRequest(BaseModel):
    comment: str | None = None


class CorrectFieldRequest(BaseModel):
    field_name: str
    original_value: str | None = None
    corrected_value: str


@router.post("/{document_id}/approve")
async def approve_document(
    document_id: uuid.UUID, payload: ApproveRequest,
    current_user: CurrentUser, db: DB,
) -> dict:
    doc = await _get_doc_or_404(db, document_id, current_user.tenant_id)
    doc.status = DocumentStatus.APPROVED
    db.add(ReviewAction(
        document_id=document_id, tenant_id=current_user.tenant_id,
        user_id=current_user.id, action_type=ReviewActionType.APPROVE,
        comment=payload.comment,
    ))
    db.add(AuditLog(
        document_id=document_id, tenant_id=current_user.tenant_id,
        user_id=current_user.id, actor=current_user.email,
        event_type="document_approved", description="Reviewer approved document",
        event_metadata={"comment": payload.comment},
    ))
    await db.flush()
    return {"status": "approved", "document_id": str(document_id)}


@router.post("/{document_id}/reject")
async def reject_document(
    document_id: uuid.UUID, payload: RejectRequest,
    current_user: CurrentUser, db: DB,
) -> dict:
    doc = await _get_doc_or_404(db, document_id, current_user.tenant_id)
    doc.status = DocumentStatus.REJECTED
    db.add(ReviewAction(
        document_id=document_id, tenant_id=current_user.tenant_id,
        user_id=current_user.id, action_type=ReviewActionType.REJECT,
        comment=payload.comment,
    ))
    db.add(AuditLog(
        document_id=document_id, tenant_id=current_user.tenant_id,
        user_id=current_user.id, actor=current_user.email,
        event_type="document_rejected",
        description=f"Reviewer rejected document: {payload.comment or ''}",
    ))
    await db.flush()
    return {"status": "rejected", "document_id": str(document_id)}


@router.post("/{document_id}/correct")
async def correct_field(
    document_id: uuid.UUID, payload: CorrectFieldRequest,
    current_user: CurrentUser, db: DB,
) -> dict:
    await _get_doc_or_404(db, document_id, current_user.tenant_id)

    # Update the extracted field value
    field = (await db.execute(
        select(ExtractedField).where(
            ExtractedField.document_id == document_id,
            ExtractedField.tenant_id == current_user.tenant_id,
            ExtractedField.field_name == payload.field_name,
        ).order_by(ExtractedField.created_at.desc())
    )).scalar_one_or_none()

    if field:
        field.value = payload.corrected_value
        from app.models.extraction import FieldStatus
        field.status = FieldStatus.CORRECTED

    db.add(ReviewAction(
        document_id=document_id, tenant_id=current_user.tenant_id,
        user_id=current_user.id, action_type=ReviewActionType.CORRECTION,
        field_name=payload.field_name,
        original_value=payload.original_value,
        corrected_value=payload.corrected_value,
    ))
    db.add(AuditLog(
        document_id=document_id, tenant_id=current_user.tenant_id,
        user_id=current_user.id, actor=current_user.email,
        event_type="field_corrected",
        description=f"Field '{payload.field_name}' corrected",
        event_metadata={
            "field": payload.field_name,
            "from": payload.original_value,
            "to": payload.corrected_value,
        },
    ))
    await db.flush()
    return {"status": "corrected", "field_name": payload.field_name}


# ── Page image for PDF viewer ─────────────────────────────────────────────────

@router.get("/{document_id}/page/{page_number}")
async def get_page_data(
    document_id: uuid.UUID, page_number: int,
    current_user: CurrentUser, db: DB,
) -> dict:
    """Return raw_text and word boxes for one page (used by evidence highlighting)."""
    from app.models.document import DocumentPage
    page = (await db.execute(
        select(DocumentPage).where(
            DocumentPage.document_id == document_id,
            DocumentPage.tenant_id == current_user.tenant_id,
            DocumentPage.page_number == page_number,
        )
    )).scalar_one_or_none()

    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    return {
        "page_number": page.page_number,
        "width": page.width,
        "height": page.height,
        "raw_text": page.raw_text,
        "words": page.words,
    }


class BatchUploadResponse(BaseModel):
    queued: List[UploadResponse]
    errors: List[dict]  # [{filename, reason}] for files that were rejected


@router.post("/batch-upload", response_model=BatchUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def batch_upload_documents(
    files: List[UploadFile],
    request: Request,
    current_user: CurrentUser,
    db: DB,
) -> BatchUploadResponse:
    """
    Upload 1–10 documents in a single request.
    Each file is validated individually; invalid files are reported in `errors`
    without blocking the valid ones. At most 10 files per request.
    """
    await rate_limit(request, "batch_upload", tenant_id=str(current_user.tenant_id))

    if len(files) > 10:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Maximum 10 files per batch upload.",
        )
    if len(files) == 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No files provided.")

    queued: list[UploadResponse] = []
    errors: list[dict] = []

    tenant_dir = Path(settings.upload_dir) / str(current_user.tenant_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)

    for file in files:
        fname = file.filename or "unknown"
        try:
            if file.content_type not in ALLOWED_MIME_TYPES:
                errors.append({"filename": fname, "reason": f"Unsupported type: {file.content_type}"})
                continue

            content = await file.read()

            if len(content) == 0:
                errors.append({"filename": fname, "reason": "File is empty"})
                continue

            if len(content) > settings.max_upload_size_bytes:
                errors.append({"filename": fname, "reason": f"File exceeds {settings.max_upload_size_mb}MB"})
                continue

            doc_id = uuid.uuid4()
            ext = ALLOWED_MIME_TYPES[file.content_type]
            storage_path = tenant_dir / f"{doc_id}.{ext}"

            async with aiofiles.open(storage_path, "wb") as f:
                await f.write(content)

            document = Document(
                id=doc_id, tenant_id=current_user.tenant_id, uploaded_by=current_user.id,
                original_filename=fname,
                storage_path=str(storage_path), file_size_bytes=len(content),
                mime_type=file.content_type, status=DocumentStatus.QUEUED,
            )
            db.add(document)
            db.add(AuditLog(
                document_id=doc_id, tenant_id=current_user.tenant_id,
                user_id=current_user.id, actor=current_user.email,
                event_type="document_uploaded",
                description=f"Batch uploaded '{fname}'",
                event_metadata={"filename": fname, "size_bytes": len(content), "batch": True},
            ))
            await db.flush()

            task = process_document.delay(
                document_id=str(doc_id),
                tenant_id=str(current_user.tenant_id),
                storage_path=str(storage_path),
            )
            document.celery_task_id = task.id
            await db.flush()

            queued.append(UploadResponse(
                document_id=doc_id, status=DocumentStatus.QUEUED,
                task_id=task.id, message=f"'{fname}' queued for processing",
            ))

        except HTTPException:
            raise  # re-raise rate limit etc.
        except Exception as exc:
            logger.exception("batch_upload_file_error", filename=fname, error=str(exc))
            errors.append({"filename": fname, "reason": "Internal error during upload"})

    return BatchUploadResponse(queued=queued, errors=errors)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_doc_or_404(db, document_id: uuid.UUID, tenant_id: uuid.UUID) -> Document:
    doc = (await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc
