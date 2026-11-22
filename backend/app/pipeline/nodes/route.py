"""
Route node — runs the YAML rules engine to produce a routing decision.

The rules engine is given:
  - validation result counts (errors, warnings)
  - extracted document data (invoice total, currency)
  - matching PO data (total, currency)
  - document quality metrics (grounding_rate, confidence)

The first matching rule in priority order decides the outcome:
  auto_approve, manager_approval, or exception_queue.

The full evaluation log (which rules were checked, which matched) is stored
in the routing_decisions table for auditability.
"""

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.routing import RoutingDecision, RoutingDecisionType
from app.pipeline.state import PipelineState
from app.rules.engine import build_context, evaluate_rules
from app.schemas.extraction import DocumentType
from app.services.audit import log_event_sync
from app.validation.cross_document import lookup_po

logger = get_logger(__name__)

RULES_FILE = "default_rules.yaml"

# Map engine decision strings to DB enum values
DECISION_MAP = {
    "auto_approve": RoutingDecisionType.AUTO_APPROVE,
    "manager_approval": RoutingDecisionType.MANAGER_APPROVAL,
    "exception_queue": RoutingDecisionType.EXCEPTION_QUEUE,
    "needs_review": RoutingDecisionType.EXCEPTION_QUEUE,  # fallback
}


def run_route_node(state: PipelineState, db: Session) -> PipelineState:
    """Route node entry point."""
    if state.error:
        return state

    logger.info("route_node_start", document_id=str(state.document_id))

    try:
        # ── Build extraction data dict for context ────────────────────────────
        extraction_data = _get_extraction_dict(state)
        extraction_data["_grounding_rate"] = state.grounding_rate
        extraction_data["_overall_confidence"] = state.overall_confidence

        # ── Look up PO data ───────────────────────────────────────────────────
        po_data = None
        if state.extraction_invoice and state.extraction_invoice.po_reference:
            po_ref = state.extraction_invoice.po_reference.value
            po = lookup_po(db, state.tenant_id, po_ref)
            if po:
                po_data = {
                    "total_amount": po.total_amount,
                    "currency": po.currency,
                    "supplier_name": po.supplier_name,
                }

        # ── Build context and evaluate rules ──────────────────────────────────
        all_validation_results = (
            state.validation_errors
            + state.validation_warnings
            + state.validation_infos
        )

        context = build_context(
            validation_results=all_validation_results,
            extraction_data=extraction_data,
            po_data=po_data,
        )

        result = evaluate_rules(context, rules_file=RULES_FILE)

        # ── Update state ──────────────────────────────────────────────────────
        state.routing_decision = result.decision
        state.routing_rule_id = result.rule_id
        state.routing_reason = result.reason

        # ── Persist to DB ─────────────────────────────────────────────────────
        db_decision = RoutingDecision(
            document_id=state.document_id,
            tenant_id=state.tenant_id,
            decision=DECISION_MAP.get(result.decision, RoutingDecisionType.EXCEPTION_QUEUE),
            rule_id=result.rule_id,
            reason=result.reason,
            evaluation_context={
                "context": context,
                "evaluation_log": result.evaluation_log,
            },
            rules_version=RULES_FILE,
        )
        db.add(db_decision)

        log_event_sync(
            db,
            tenant_id=state.tenant_id,
            document_id=state.document_id,
            event_type="routing_decision",
            description=f"Routed to '{result.decision}' via rule '{result.rule_id}'",
            metadata={
                "decision": result.decision,
                "rule_id": result.rule_id,
                "reason": result.reason,
            },
        )
        db.commit()

        logger.info(
            "route_node_complete",
            document_id=str(state.document_id),
            decision=result.decision,
            rule_id=result.rule_id,
        )

    except Exception as exc:
        logger.exception("route_node_failed", document_id=str(state.document_id), error=str(exc))
        # Non-fatal: default to exception_queue
        state.routing_decision = "exception_queue"
        state.routing_reason = f"Routing failed with error: {exc}"

    return state


def _get_extraction_dict(state: PipelineState) -> dict:
    """Return a flat dict of extraction data for the rules context."""
    schema = (
        state.extraction_invoice
        or state.extraction_bol
        or state.extraction_packing
    )
    if schema is None:
        return {}
    return schema.model_dump()
