"""
Validate node — runs all deterministic checks after extraction and grounding.

Runs in order:
  1. Document-type specific validators (required fields, formats, math)
  2. Cross-document validators (invoice vs PO, invoice vs BoL)

Results are written to the validation_results table and stored on state
so the route node and retry node can use them.
"""

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.validation import (
    ValidationResult,
    ValidationSeverity,
    ValidationStatus,
)
from app.pipeline.state import PipelineState
from app.schemas.extraction import DocumentType
from app.services.audit import log_event_sync
from app.validation.cross_document import (
    lookup_po,
    validate_invoice_vs_bol,
    validate_invoice_vs_po,
)
from app.validation.types import CheckResult, CheckStatus, Severity
from app.validation.validators import (
    validate_bill_of_lading,
    validate_commercial_invoice,
    validate_packing_list,
)

logger = get_logger(__name__)


def run_validate_node(state: PipelineState, db: Session) -> PipelineState:
    """
    Validate node entry point.
    Runs all checks and populates state.validation_errors/warnings/infos.
    """
    if state.error:
        return state

    logger.info("validate_node_start", document_id=str(state.document_id))

    try:
        doc_type = state.classification.doc_type if state.classification else None
        all_results: list[CheckResult] = []

        # ── Document-type validators ──────────────────────────────────────────
        if doc_type == DocumentType.BILL_OF_LADING and state.extraction_bol:
            all_results.extend(validate_bill_of_lading(state.extraction_bol))

        elif doc_type == DocumentType.COMMERCIAL_INVOICE and state.extraction_invoice:
            all_results.extend(validate_commercial_invoice(state.extraction_invoice))

            # Cross-document: invoice vs PO
            po_ref = None
            if state.extraction_invoice.po_reference:
                po_ref = state.extraction_invoice.po_reference.value
            po = lookup_po(db, state.tenant_id, po_ref)
            all_results.extend(validate_invoice_vs_po(state.extraction_invoice, po))

            # Cross-document: invoice vs BoL (future — BoL would be looked up by shipment ref)
            # all_results.extend(validate_invoice_vs_bol(state.extraction_invoice, bol))

        elif doc_type == DocumentType.PACKING_LIST and state.extraction_packing:
            all_results.extend(validate_packing_list(state.extraction_packing))

        # ── Persist results to DB ─────────────────────────────────────────────
        _persist_validation_results(db, state.document_id, state.tenant_id, all_results)

        # ── Bucket results into state ─────────────────────────────────────────
        state.validation_errors = [
            r.to_dict() for r in all_results
            if r.failed and r.severity == Severity.ERROR
        ]
        state.validation_warnings = [
            r.to_dict() for r in all_results
            if r.failed and r.severity == Severity.WARNING
        ]
        state.validation_infos = [
            r.to_dict() for r in all_results
            if r.passed
        ]

        # Add field-level errors to fields_to_retry if we haven't hit max retries
        field_errors = [r for r in all_results if r.failed and r.field_name]
        existing_retry_names = {f["field_name"] for f in state.fields_to_retry}
        for r in field_errors:
            if r.field_name not in existing_retry_names:
                state.fields_to_retry.append({
                    "field_name": r.field_name,
                    "reason": r.message,
                })

        error_count = len(state.validation_errors)
        warning_count = len(state.validation_warnings)

        log_event_sync(
            db,
            tenant_id=state.tenant_id,
            document_id=state.document_id,
            event_type="validation_complete",
            description=f"Validation: {error_count} errors, {warning_count} warnings",
            metadata={
                "error_count": error_count,
                "warning_count": warning_count,
                "total_checks": len(all_results),
            },
        )
        db.commit()

        logger.info(
            "validate_node_complete",
            document_id=str(state.document_id),
            errors=error_count,
            warnings=warning_count,
        )

    except Exception as exc:
        logger.exception("validate_node_failed", document_id=str(state.document_id), error=str(exc))
        # Validation failure is non-fatal — log it but continue to routing
        state.validation_errors.append({
            "check_name": "validate_node_exception",
            "status": "fail",
            "severity": "error",
            "message": f"Validation node raised an exception: {exc}",
            "field_name": None,
        })

    return state


def _persist_validation_results(
    db: Session,
    document_id,
    tenant_id,
    results: list[CheckResult],
) -> None:
    """Write all check results to validation_results table."""
    from sqlalchemy import delete

    # Clear any previous results (idempotent)
    db.execute(
        delete(ValidationResult).where(
            ValidationResult.document_id == document_id,
            ValidationResult.tenant_id == tenant_id,
        )
    )

    severity_map = {
        Severity.INFO: ValidationSeverity.INFO,
        Severity.WARNING: ValidationSeverity.WARNING,
        Severity.ERROR: ValidationSeverity.ERROR,
    }
    status_map = {
        CheckStatus.PASS: ValidationStatus.PASS,
        CheckStatus.FAIL: ValidationStatus.FAIL,
    }

    for r in results:
        db.add(ValidationResult(
            document_id=document_id,
            tenant_id=tenant_id,
            check_name=r.check_name,
            status=status_map[r.status],
            severity=severity_map[r.severity],
            message=r.message,
            field_name=r.field_name,
        ))
