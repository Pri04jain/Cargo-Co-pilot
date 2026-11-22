"""
Celery tasks for document processing.

Phase 3: The stub pipeline is replaced with the real LangGraph pipeline.
The task now calls run_pipeline() which executes all nodes end-to-end.
"""

try:
    import sqlite_compat  # noqa: F401
except ImportError:
    pass
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
    Celery workers are sync — we use the sync engine and a regular Session.
    A new session is created per task invocation (not shared between tasks).
    """
    _db: Optional[Session] = None

    @property
    def db(self) -> Session:
        if self._db is None or not self._db.is_active:
            self._db = Session(sync_engine)
        return self._db

    def after_return(self, status, retval, task_id, args, kwargs, einfo):
        """Close the DB session after the task finishes (success or failure)."""
        if self._db is not None:
            self._db.close()
            self._db = None


@celery_app.task(
    bind=True,
    base=DocumentTask,
    name="process_document",
    max_retries=3,
    default_retry_delay=30,
)
def process_document(
    self,
    document_id: str,
    tenant_id: str,
    storage_path: str,
) -> dict:
    """
    Main document processing task.

    Flow:
      1. Mark document as processing
      2. Run the LangGraph pipeline (parse → classify → extract → ground → retry → finalize)
      3. The pipeline's finalize node sets the final document status
      4. On unexpected failure, mark as failed and retry via Celery backoff
    """
    doc_uuid = uuid.UUID(document_id)
    tenant_uuid = uuid.UUID(tenant_id)

    logger.info("task_started", document_id=document_id, task_id=self.request.id)

    try:
        # ── Mark as processing ────────────────────────────────────────────────
        _update_status(self.db, doc_uuid, tenant_uuid, DocumentStatus.PROCESSING, self.request.id)

        # ── Run the real pipeline ─────────────────────────────────────────────
        from app.pipeline.graph import run_pipeline

        state = run_pipeline(
            db=self.db,
            document_id=document_id,
            tenant_id=tenant_id,
            storage_path=storage_path,
        )

        if state.error:
            logger.warning(
                "pipeline_finished_with_error",
                document_id=document_id,
                error=state.error,
            )
            return {"status": "failed", "document_id": document_id, "error": state.error}

        logger.info(
            "task_completed",
            document_id=document_id,
            status=state.routing_decision,
            grounding_rate=state.grounding_rate,
            total_cost_usd=state.total_cost_usd,
        )
        return {
            "status": state.routing_decision or "needs_review",
            "document_id": document_id,
            "grounding_rate": state.grounding_rate,
            "total_cost_usd": state.total_cost_usd,
        }

    except Exception as exc:
        logger.exception("task_failed", document_id=document_id, error=str(exc))
        try:
            _mark_failed(self.db, doc_uuid, tenant_uuid, str(exc))
        except Exception:
            pass
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 30)


def _update_status(
    db: Session,
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    new_status: DocumentStatus,
    task_id: str,
) -> None:
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
        event_metadata={"from": old_status, "to": new_status, "task_id": task_id},
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
        doc.error_message = error_message[:2000]
        audit = AuditLog(
            document_id=document_id,
            tenant_id=tenant_id,
            actor="system",
            event_type="pipeline_failed",
            description=f"Processing failed: {error_message[:200]}",
            event_metadata={"error": error_message},
        )
        db.add(audit)
        db.commit()
