"""
LangGraph pipeline graph — wires all nodes together.

Graph structure:
  ingest → parse → classify → extract → ground → [validate] →
    ↑ (retry_extract ← should_retry? yes) ↓ no
                              route → finalize

Phase 3 wires: parse → classify → extract → ground → retry loop → finalize
Validate and route nodes are added in Phase 4.

Why LangGraph over a plain function chain?
  1. Conditional edges (retry loop) are first-class: no if/else threading through functions
  2. Each node is a pure function (state in → state out) — easy to unit test
  3. The graph is visualisable (graph.get_graph().print_ascii())
  4. State is typed and explicit — no hidden global state

The sync graph runner is used by Celery workers (sync context).
"""

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.document import Document, DocumentStatus
from app.pipeline.nodes.classify import run_classify_node
from app.pipeline.nodes.extract import run_extract_node
from app.pipeline.nodes.ground import run_ground_node
from app.pipeline.nodes.parse import run_parse_node
from app.pipeline.nodes.retry_extract import MAX_RETRIES, run_retry_extract_node, should_retry
from app.pipeline.nodes.validate import run_validate_node
from app.pipeline.nodes.route import run_route_node
from app.pipeline.state import PipelineState
from app.services.audit import log_event_sync

logger = get_logger(__name__)


def run_pipeline(
    db: Session,
    document_id: str,
    tenant_id: str,
    storage_path: str,
    mime_type: str = "application/pdf",
) -> PipelineState:
    """
    Execute the full document processing pipeline.

    Returns the final PipelineState.
    state.error is set if any node failed.

    This is a simple sequential runner — not using LangGraph's async graph
    runner since Celery workers are sync. LangGraph's StateGraph is used for
    structure and visualisation; the execution loop here is intentionally simple.
    """
    import uuid

    state = PipelineState(
        document_id=uuid.UUID(document_id),
        tenant_id=uuid.UUID(tenant_id),
        storage_path=storage_path,
        mime_type=mime_type,
    )

    logger.info("pipeline_start", document_id=document_id)

    # ── Node sequence ─────────────────────────────────────────────────────────
    state = run_parse_node(state, db)
    if state.error:
        return _finalize(state, db)

    state = run_classify_node(state, db)
    if state.error:
        return _finalize(state, db)

    state = run_extract_node(state, db)
    if state.error:
        return _finalize(state, db)

    state = run_ground_node(state, db)
    if state.error:
        return _finalize(state, db)

    # ── Retry loop (max MAX_RETRIES attempts) ─────────────────────────────────
    while should_retry(state) and state.extraction_attempt < MAX_RETRIES:
        state = run_retry_extract_node(state, db)
        if state.error:
            break
        state = run_ground_node(state, db)

    # ── Validate and route (Phase 4) ──────────────────────────────────────────
    if not state.error:
        state = run_validate_node(state, db)

    if not state.error:
        state = run_route_node(state, db)

    return _finalize(state, db)


def _finalize(state: PipelineState, db: Session) -> PipelineState:
    """
    Finalize node — updates document status, writes cost totals, audit log,
    and indexes document chunks for RAG search.
    Always runs regardless of success or failure.
    """
    from sqlalchemy import select

    try:
        doc_result = db.execute(
            select(Document).where(Document.id == state.document_id)
        )
        doc = doc_result.scalar_one_or_none()
        if not doc:
            return state

        if state.error:
            doc.status = DocumentStatus.FAILED
            doc.error_message = state.error[:2000]
            new_status = DocumentStatus.FAILED
        else:
            if state.routing_decision == "auto_approve":
                doc.status = DocumentStatus.APPROVED
                new_status = DocumentStatus.APPROVED
            else:
                doc.status = DocumentStatus.NEEDS_REVIEW
                new_status = DocumentStatus.NEEDS_REVIEW

        doc.total_tokens = state.total_tokens
        doc.total_cost_usd = round(state.total_cost_usd, 6)
        doc.overall_confidence = state.overall_confidence
        doc.grounding_rate = state.grounding_rate

        log_event_sync(
            db,
            tenant_id=state.tenant_id,
            document_id=state.document_id,
            event_type="pipeline_finalized",
            description=f"Pipeline complete: {new_status}",
            metadata={
                "status": new_status,
                "total_tokens": state.total_tokens,
                "total_cost_usd": state.total_cost_usd,
                "grounding_rate": state.grounding_rate,
                "overall_confidence": state.overall_confidence,
                "extraction_attempts": state.extraction_attempt + 1,
                "error": state.error,
            },
        )
        db.commit()

        # ── Index chunks for RAG (non-fatal if it fails) ──────────────────────
        if not state.error and state.parsed_document:
            try:
                from app.rag.indexer import index_document
                pages = [
                    (p.page_number, p.raw_text or "")
                    for p in state.parsed_document.pages
                ]
                # Include key extracted fields in chunk context for hybrid search
                field_context = _extract_field_context(state)
                index_document(
                    db=db,
                    document_id=state.document_id,
                    tenant_id=state.tenant_id,
                    pages=pages,
                    field_context=field_context,
                )
            except Exception as idx_exc:
                logger.warning(
                    "chunk_indexing_failed",
                    document_id=str(state.document_id),
                    error=str(idx_exc),
                )

    except Exception as exc:
        logger.exception(
            "finalize_failed",
            document_id=str(state.document_id),
            error=str(exc),
        )
        try:
            db.rollback()
        except Exception:
            pass

    return state


def _extract_field_context(state: PipelineState) -> dict:
    """Pull key field values into a flat dict for chunk field_context storage."""
    ctx: dict = {}
    schema = state.extraction_invoice or state.extraction_bol or state.extraction_packing
    if not schema:
        return ctx

    # Extract a handful of important fields for hybrid search boost
    important_fields = [
        "invoice_number", "bl_number", "packing_list_number",
        "total_amount", "currency", "vessel", "port_of_loading", "port_of_discharge",
    ]
    for fname in important_fields:
        field = getattr(schema, fname, None)
        if field and hasattr(field, "value") and field.value:
            ctx[fname] = field.value
    return ctx
