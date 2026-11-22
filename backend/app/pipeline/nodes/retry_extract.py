"""
Retry extract node — re-extracts only failing or ungrounded fields.

Rather than re-running the entire extraction (expensive), we:
  1. Identify the specific fields that failed validation or grounding
  2. Find the page(s) most likely to contain those fields
  3. Send a focused prompt with only the relevant page text + field names
  4. Merge the new values back into the existing schema

Why targeted retry instead of full re-extraction?
  - Cheaper (fewer tokens)
  - Avoids regressing on fields that were extracted correctly
  - The focused prompt with the failure reason often produces better results
    than the general prompt did

Max retries: 2 (state.extraction_attempt 0 → 1 → 2, then give up and route)
"""

import json
from typing import Optional

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.llm.prompt_loader import render_prompt
from app.llm.provider import get_llm_client
from app.models.llm_call import LLMCall
from app.pipeline.state import PipelineState
from app.pipeline.nodes.extract import _flatten_fields, _save_llm_call, _strip_markdown
from app.schemas.extraction import DocumentType, ExtractedField, SCHEMA_BY_TYPE
from app.services.audit import log_event_sync

logger = get_logger(__name__)

MAX_RETRIES = 2
RETRY_PROMPT = "retry_extract_v1"
RETRY_TEXT_LIMIT = 6000  # chars — less than initial since we're more targeted


def should_retry(state: PipelineState) -> bool:
    """
    Conditional edge function for the LangGraph graph.
    Returns True if we should retry extraction.
    """
    if state.error:
        return False
    if state.extraction_attempt >= MAX_RETRIES:
        return False
    # Retry if there are ungrounded fields or validation errors pointing to specific fields
    has_retry_fields = bool(state.fields_to_retry)
    has_field_errors = any(
        v.get("field_name") for v in state.validation_errors
    )
    return has_retry_fields or has_field_errors


def run_retry_extract_node(state: PipelineState, db: Session) -> PipelineState:
    """
    Retry extract node entry point.
    Only called when should_retry() returns True.
    """
    if state.error:
        return state

    state.extraction_attempt += 1

    logger.info(
        "retry_extract_node_start",
        document_id=str(state.document_id),
        attempt=state.extraction_attempt,
        fields_to_retry=[f["field_name"] for f in state.fields_to_retry],
    )

    try:
        doc_type = state.classification.doc_type if state.classification else None
        if not doc_type or doc_type == DocumentType.UNKNOWN:
            logger.warning("retry_no_doc_type", document_id=str(state.document_id))
            return state

        schema_class = SCHEMA_BY_TYPE[doc_type]
        client = get_llm_client()

        # Build the list of fields to retry with reasons
        retry_fields = _build_retry_list(state)
        if not retry_fields:
            return state

        relevant_text = _get_relevant_text(state, retry_fields)

        # Build fields + reasons description for the prompt
        fields_desc = "\n".join(
            f"- {f['field_name']}: {f['reason']}" for f in retry_fields
        )
        reasons_desc = fields_desc  # same content, used in both prompt vars

        user_prompt = render_prompt(
            RETRY_PROMPT,
            fields_to_retry=fields_desc,
            retry_reasons=reasons_desc,
            relevant_text=relevant_text,
            schema=json.dumps(schema_class.model_json_schema(), indent=2),
        )

        system = (
            "You are re-extracting specific fields from a shipping document. "
            "Focus only on the fields listed. Return valid JSON. "
            "For each field include value, confidence, and evidence with snippet."
        )

        llm_response = client.complete(
            system=system,
            user=user_prompt,
            temperature=0.0,
            max_tokens=8192,
            trace_name=f"retry_extract_attempt_{state.extraction_attempt}",
            prompt_version=RETRY_PROMPT,
            document_id=str(state.document_id),
        )

        # Parse and merge new values
        content = _strip_markdown(llm_response.content)
        data = json.loads(content)
        new_schema = schema_class.model_validate(data)

        # Merge: only update fields that were in the retry list
        retry_field_names = {f["field_name"] for f in retry_fields}
        merged = _merge_schemas(
            existing=_get_current_schema(state),
            new=new_schema,
            fields_to_update=retry_field_names,
            schema_class=schema_class,
        )

        # Put merged schema back in state
        _update_state_schema(state, doc_type, merged)

        state.total_tokens += llm_response.total_tokens
        state.total_cost_usd += llm_response.cost_usd

        _save_llm_call(
            db, state.document_id, state.tenant_id,
            node=f"retry_extract_attempt_{state.extraction_attempt}",
            prompt_version=RETRY_PROMPT,
            llm_response=llm_response,
        )

        log_event_sync(
            db,
            tenant_id=state.tenant_id,
            document_id=state.document_id,
            event_type="retry_extraction_complete",
            description=(
                f"Retry attempt {state.extraction_attempt}: "
                f"re-extracted {len(retry_fields)} fields"
            ),
            metadata={
                "attempt": state.extraction_attempt,
                "retry_fields": [f["field_name"] for f in retry_fields],
                "tokens": llm_response.total_tokens,
            },
        )
        db.commit()

        logger.info(
            "retry_extract_node_complete",
            document_id=str(state.document_id),
            attempt=state.extraction_attempt,
            fields_retried=len(retry_fields),
        )

    except Exception as exc:
        # Retry failure is non-fatal — we continue with whatever we have
        logger.warning(
            "retry_extract_node_failed",
            document_id=str(state.document_id),
            error=str(exc),
        )

    return state


def _build_retry_list(state: PipelineState) -> list[dict]:
    """
    Combine fields_to_retry from grounding with field-level validation errors.
    Deduplicate by field name.
    """
    seen = set()
    retry_list = []

    for item in state.fields_to_retry:
        name = item["field_name"]
        if name not in seen:
            seen.add(name)
            retry_list.append(item)

    for err in state.validation_errors:
        name = err.get("field_name")
        if name and name not in seen:
            seen.add(name)
            retry_list.append({
                "field_name": name,
                "reason": f"Validation error: {err.get('message', 'unknown')}",
            })

    return retry_list


def _get_relevant_text(state: PipelineState, retry_fields: list[dict]) -> str:
    """Return the page text most likely to contain the retry fields."""
    if not state.parsed_document:
        return ""
    # For now use full text (truncated) — Phase 7 can improve with field-page mapping
    return state.parsed_document.full_text[:RETRY_TEXT_LIMIT]


def _get_current_schema(state: PipelineState):
    return state.extraction_bol or state.extraction_invoice or state.extraction_packing


def _update_state_schema(state: PipelineState, doc_type, schema) -> None:
    if doc_type == DocumentType.BILL_OF_LADING:
        state.extraction_bol = schema
    elif doc_type == DocumentType.COMMERCIAL_INVOICE:
        state.extraction_invoice = schema
    elif doc_type == DocumentType.PACKING_LIST:
        state.extraction_packing = schema


def _merge_schemas(existing, new, fields_to_update: set[str], schema_class) -> object:
    """
    Merge new schema into existing, only for fields in fields_to_update.
    Fields not in the retry list keep their original values.
    """
    existing_flat = dict(_flatten_fields(existing))
    new_flat = dict(_flatten_fields(new))

    existing_dict = existing.model_dump()

    # Patch only the fields that were retried and actually have a new value
    for field_name in fields_to_update:
        new_field = new_flat.get(field_name)
        if new_field and new_field.value is not None:
            _patch_field_in_dict(existing_dict, field_name, new_field.model_dump())

    return schema_class.model_validate(existing_dict)


def _patch_field_in_dict(d: dict, field_path: str, new_value: dict) -> None:
    """
    Navigate a nested dict by dot-separated path and replace the leaf value.
    Supports array notation: field_name[0].subfield
    """
    import re
    parts = re.split(r'[.\[]', field_path)
    parts = [p.rstrip(']') for p in parts if p]

    current = d
    for i, part in enumerate(parts[:-1]):
        if part.isdigit():
            current = current[int(part)]
        elif part in current:
            current = current[part]
        else:
            return  # path not found, skip

    last = parts[-1]
    if last.isdigit():
        if isinstance(current, list) and int(last) < len(current):
            current[int(last)] = new_value
    elif last in current:
        current[last] = new_value
