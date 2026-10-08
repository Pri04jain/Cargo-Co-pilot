"""
Celery tasks for document processing.

Phase 1: A stub pipeline that moves the document through status transitions.
The real pipeline (LangGraph nodes) is wired in Phase 3.

Why a stub?
- Proves the queue/worker infrastructure works end-to-end before adding LLM complexity.
- The status transitions and error handling patterns are the same regardless of
  what's inside the task.
"""

import time
import uuid
from typing import Optional

from celery import Task
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import sync_engine
from app.models.audit import AuditLog
from app.models.document import Document, DocumentStatus
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


class DocumentTask(Task):
    """
    Base task class with a sync DB session.
    Celery workers run in a sync context, so we use the sync engine here.
    The session is created fresh per task (not shared between tasks).
    """
    _db: Optional[Session] = None

    @property
    def db(self) -> Session:
        if self._db is None or not self._db.is_active:
            self._db = Session(sync_engine)
        return self._db

    def after_return(self, status, retval, task_id, args, kwargs, einfo):
        """Close the DB session after the task completes."""
        if self._db is not None:
            self._db.close()
            self._db = None


@celery_app.task(
    bind=True,
    base=DocumentTask,
    name="process_document",
    max_retries=3,
    default_retry_delay=30,  # seconds between automatic retries
)
def process_document(
    self,
    document_id: str,
    tenant_id: str,
    storage_path: str,
) -> dict:
    """
    Main document processing task.

    Phase 1 stub: just moves through status transitions with a simulated delay.
    Phase 3+ will replace the body with the LangGraph pipeline invocation.
    """
    doc_uuid = uuid.UUID(document_id)
    tenant_uuid = uuid.UUID(tenant_id)

    logger.info("task_started", document_id=document_id, task_id=self.request.id)

    try:
        # ── Mark as processing ────────────────────────────────────────────────
        _update_status(self.db, doc_uuid, tenant_uuid, DocumentStatus.PROCESSING, self.request.id)

        # ── Phase 1 stub: simulate work ───────────────────────────────────────
        # TODO Phase 3: replace with actual LangGraph pipeline
        # pipeline_result = run_pipeline(document_id, tenant_id, storage_path)
        time.sleep(2)  # simulate parsing/extraction time

        # ── Move to needs_review (default until routing says auto_approve) ────
        _update_status(self.db, doc_uuid, tenant_uuid, DocumentStatus.NEEDS_REVIEW, self.request.id)

        logger.info("task_completed", document_id=document_id)
        return {"status": "needs_review", "document_id": document_id}

    except Exception as exc:
        logger.exception("task_failed", document_id=document_id, error=str(exc))

        # Mark the document as failed with the error message
        try:
            _mark_failed(self.db, doc_uuid, tenant_uuid, str(exc))
        except Exception:
            pass  # don't let error logging mask the original error

        # Retry with exponential backoff up to max_retries
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 30)


def _update_status(
    db: Session,
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    new_status: DocumentStatus,
    task_id: str,
) -> None:
    """Update document status and write an audit log entry."""
    result = db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise ValueError(f"Document {document_id} not found")

    old_status = doc.status
    doc.status = new_status

    audit = AuditLog(
        document_id=document_id,
        tenant_id=tenant_id,
        actor="system",
        event_type="status_changed",
        description=f"Status changed: {old_status} → {new_status}",
        metadata={"from": old_status, "to": new_status, "task_id": task_id},
    )
    db.add(audit)
    db.commit()


def _mark_failed(
    db: Session,
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    error_message: str,
) -> None:
    result = db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc:
        doc.status = DocumentStatus.FAILED
        doc.error_message = error_message[:2000]  # truncate to column limit

        audit = AuditLog(
            document_id=document_id,
            tenant_id=tenant_id,
            actor="system",
            event_type="pipeline_failed",
            description=f"Processing failed: {error_message[:200]}",
            metadata={"error": error_message},
        )
        db.add(audit)
        db.commit()
