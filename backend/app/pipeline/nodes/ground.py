"""
Ground node — matches LLM-returned snippets to word bounding boxes.

After extraction every ExtractedField has:
  - value: the extracted string
  - evidence.snippet: the text the LLM cited as its source

This node:
  1. Loads the parsed word list per page from the DB
  2. Calls ground_field() for every ExtractedField in the schema
  3. Updates each field's status (grounded/ungrounded) and bbox
  4. Computes grounding_rate and overall_confidence for the document
  5. Updates the extracted_fields table with the grounding results
  6. Populates state.fields_to_retry (fields that are ungrounded or low-confidence)

The grounding rate is a key quality metric:
  - High grounding rate (>80%) → extraction is trustworthy
  - Low grounding rate (<50%) → something went wrong (bad OCR, wrong doc type)
"""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.document import DocumentPage
from app.models.extraction import ExtractedField as DBField
from app.models.extraction import FieldStatus as DBFieldStatus
from app.parsers.grounder import ground_field
from app.parsers.types import WordBox
from app.pipeline.state import PipelineState
from app.schemas.extraction import (
    BillOfLadingSchema,
    CommercialInvoiceSchema,
    DocumentType,
    ExtractedField,
    FieldStatus,
    PackingListSchema,
)
from app.services.audit import log_event_sync

logger = get_logger(__name__)

# Fields with confidence below this after grounding trigger a retry
LOW_CONFIDENCE_THRESHOLD = 0.5


def run_ground_node(state: PipelineState, db: Session) -> PipelineState:
    """
    Ground node entry point.
    Updates extraction fields with bbox evidence and computes aggregate metrics.
    """
    if state.error:
        return state

    logger.info("ground_node_start", document_id=str(state.document_id))

    try:
        # Load word boxes per page from the DB
        words_by_page = _load_words_by_page(db, state.document_id, state.tenant_id)

        # Get the current extraction schema from state
        schema = _get_current_schema(state)
        if schema is None:
            state.error = "Ground node: no extraction schema found in state"
            state.failed_node = "ground"
            return state

        # Run grounding on all fields
        grounded_schema, field_results, grounded_fields = _ground_all_fields(schema, words_by_page)

        # Update state with grounded schema
        doc_type = state.classification.doc_type if state.classification else None
        _update_state_schema(state, doc_type, grounded_schema)

        # Compute aggregate metrics
        total_fields = len(field_results)
        grounded_count = sum(1 for _, _, s in field_results if s == FieldStatus.GROUNDED)
        confidence_sum = sum(c for _, c, _ in field_results if c > 0)
        non_null_count = sum(1 for v, _, _ in field_results if v is not None)

        state.grounding_rate = round(grounded_count / max(total_fields, 1), 3)
        state.overall_confidence = round(confidence_sum / max(non_null_count, 1), 3)

        # Identify fields that need retry (ungrounded or low confidence)
        state.fields_to_retry = [
            {"field_name": name, "reason": reason}
            for name, conf, status in field_results
            for reason in [_retry_reason(conf, status)]
            if reason is not None
            for name in [name]  # flatten
        ]
        # Rebuild properly
        state.fields_to_retry = []
        for name, conf, status in field_results:
            reason = _retry_reason(conf, status)
            if reason:
                state.fields_to_retry.append({"field_name": name, "reason": reason})

        # Update the DB records
        _update_db_fields(db, state.document_id, state.tenant_id, field_results, grounded_fields)

        log_event_sync(
            db,
            tenant_id=state.tenant_id,
            document_id=state.document_id,
            event_type="grounding_complete",
            description=(
                f"Grounded {grounded_count}/{total_fields} fields "
                f"(rate={state.grounding_rate:.1%}, "
                f"confidence={state.overall_confidence:.2f})"
            ),
            metadata={
                "grounding_rate": state.grounding_rate,
                "overall_confidence": state.overall_confidence,
                "fields_to_retry": len(state.fields_to_retry),
            },
        )
        db.commit()

        logger.info(
            "ground_node_complete",
            document_id=str(state.document_id),
            grounding_rate=state.grounding_rate,
            overall_confidence=state.overall_confidence,
            fields_to_retry=len(state.fields_to_retry),
        )

    except Exception as exc:
        logger.exception("ground_node_failed", document_id=str(state.document_id), error=str(exc))
        state.error = f"Grounding failed: {exc}"
        state.failed_node = "ground"

    return state


def _load_words_by_page(db: Session, document_id, tenant_id) -> dict[int, list[WordBox]]:
    """Load parsed word boxes from document_pages table."""
    result = db.execute(
        select(DocumentPage).where(
            DocumentPage.document_id == document_id,
            DocumentPage.tenant_id == tenant_id,
        )
    )
    pages = result.scalars().all()

    words_by_page: dict[int, list[WordBox]] = {}
    for page in pages:
        if not page.words:
            continue
        word_list = page.words.get("words", [])
        words_by_page[page.page_number] = [
            WordBox(
                text=w["text"],
                x0=w["x0"], y0=w["y0"],
                x1=w["x1"], y1=w["y1"],
                page=w["page"],
            )
            for w in word_list
            if w.get("text", "").strip()
        ]
    return words_by_page


def _get_current_schema(state: PipelineState):
    """Return whichever extraction schema is populated in state."""
    return state.extraction_bol or state.extraction_invoice or state.extraction_packing


def _ground_all_fields(
    schema,
    words_by_page: dict[int, list[WordBox]],
) -> tuple[object, list[tuple[str, float, FieldStatus]]]:
    """
    Run ground_field() on every ExtractedField in the schema.
    Returns (updated_schema, [(field_name, confidence, status), ...]).

    We rebuild the schema by constructing a dict of updated field values
    and passing it to model_validate(). This keeps the schema immutable
    (Pydantic models are immutable by default).
    """
    from app.pipeline.nodes.extract import _flatten_fields

    field_results: list[tuple[str, float, FieldStatus]] = []
    grounded_fields: dict[str, ExtractedField] = {}

    # Flatten, ground each field, collect results
    flat = _flatten_fields(schema)
    for field_name, field_obj in flat:
        if field_obj is None or field_obj.value is None:
            continue
        grounded = ground_field(field_obj, words_by_page)
        grounded_fields[field_name] = grounded
        field_results.append((field_name, grounded.confidence, grounded.status))

    # Rebuild schema with grounded fields
    # For now we update in-place on the mutable dataclass-like Pydantic model
    # (Pydantic v2 allows model_copy for immutable updates)
    updated = _apply_grounded_fields(schema, grounded_fields)

    return updated, field_results, grounded_fields


def _apply_grounded_fields(schema, grounded_fields: dict[str, ExtractedField]):
    """
    Walk the schema and replace ExtractedField instances with their grounded versions.
    Uses recursive dict-patching and model_validate to rebuild immutably.
    """
    raw = schema.model_dump()
    _patch_dict(raw, grounded_fields, prefix="")
    return schema.__class__.model_validate(raw)


def _patch_dict(d: dict, grounded: dict[str, ExtractedField], prefix: str) -> None:
    """Recursively patch a dict with grounded field values."""
    for key, value in d.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and "value" in value and "confidence" in value:
            # This looks like an ExtractedField
            if full_key in grounded:
                gf = grounded[full_key]
                d[key] = gf.model_dump()
        elif isinstance(value, dict):
            _patch_dict(value, grounded, full_key)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                item_key = f"{full_key}[{i}]"
                if isinstance(item, dict):
                    if "value" in item and "confidence" in item and item_key in grounded:
                        value[i] = grounded[item_key].model_dump()
                    else:
                        _patch_dict(item, grounded, item_key)


def _update_state_schema(state: PipelineState, doc_type, grounded_schema) -> None:
    if doc_type == DocumentType.BILL_OF_LADING:
        state.extraction_bol = grounded_schema
    elif doc_type == DocumentType.COMMERCIAL_INVOICE:
        state.extraction_invoice = grounded_schema
    elif doc_type == DocumentType.PACKING_LIST:
        state.extraction_packing = grounded_schema


def _retry_reason(confidence: float, status: FieldStatus) -> str | None:
    """Return a reason string if this field should be retried, else None."""
    if status == FieldStatus.UNGROUNDED:
        return "Field could not be grounded in document text"
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        return f"Low confidence ({confidence:.2f}) after grounding"
    return None


def _update_db_fields(db: Session, document_id, tenant_id, field_results, grounded_fields: dict):
    """
    Update extracted_fields in the DB with grounding status, confidence, and evidence bbox.
    """
    result = db.execute(
        select(DBField).where(
            DBField.document_id == document_id,
            DBField.tenant_id == tenant_id,
        )
    )
    db_fields = {f.field_name: f for f in result.scalars().all()}

    status_map = {
        FieldStatus.GROUNDED: DBFieldStatus.GROUNDED,
        FieldStatus.UNGROUNDED: DBFieldStatus.UNGROUNDED,
        FieldStatus.CORRECTED: DBFieldStatus.CORRECTED,
    }

    for field_name, confidence, status in field_results:
        if field_name not in db_fields:
            continue
        db_field = db_fields[field_name]
        db_field.confidence = confidence
        db_field.status = status_map.get(status, DBFieldStatus.UNGROUNDED)

        # Save evidence (bbox + snippet + page) if grounding succeeded
        grounded = grounded_fields.get(field_name)
        if grounded and grounded.evidence:
            ev = grounded.evidence
            import copy
            db_field.evidence = copy.deepcopy({
                "page": ev.page,
                "snippet": ev.snippet or "",
                "bbox": ev.bbox.model_dump() if ev.bbox else None,
            })
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(db_field, "evidence")
