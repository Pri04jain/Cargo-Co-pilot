"""
Classify node — determines the document type (BoL, Invoice, Packing List).

Strategy:
  1. Take the first 3000 characters of page 1 text (usually enough to classify)
  2. Call LLM with a small schema (doc_type, confidence, reasoning)
  3. If confidence < 0.6, try a rule-based fallback using keyword matching
  4. Store the LLMCall record for cost tracking

Why rule-based fallback?
  Classification is binary-ish (3 classes) and shipping documents have very
  consistent vocabulary. If the LLM is uncertain, keyword matching is faster,
  cheaper, and often more reliable than a second LLM call.
"""

import json
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.llm.prompt_loader import render_prompt
from app.llm.provider import LLMClient, get_llm_client
from app.models.llm_call import LLMCall
from app.pipeline.state import PipelineState
from app.schemas.extraction import ClassificationResult, DocumentType
from app.services.audit import log_event_sync

logger = get_logger(__name__)

# Keyword sets for rule-based fallback classification
KEYWORDS: dict[str, list[str]] = {
    DocumentType.BILL_OF_LADING: [
        "bill of lading", "b/l", "bl number", "shipper", "consignee",
        "port of loading", "port of discharge", "vessel", "voyage",
        "container", "seal", "freight", "notify party",
    ],
    DocumentType.COMMERCIAL_INVOICE: [
        "commercial invoice", "invoice number", "invoice no", "invoice date",
        "seller", "buyer", "unit price", "line total", "subtotal",
        "total amount", "hs code", "payment terms", "incoterm",
    ],
    DocumentType.PACKING_LIST: [
        "packing list", "packing slip", "package", "carton", "net weight",
        "gross weight", "dimensions", "total packages", "total gross",
    ],
}

CLASSIFY_PROMPT = "classify_v1"
CLASSIFY_TEXT_LIMIT = 3000   # chars sent to LLM for classification
LLM_CONFIDENCE_THRESHOLD = 0.6


def run_classify_node(state: PipelineState, db: Session) -> PipelineState:
    """
    Classify node entry point.
    Sets state.classification on success, state.error on failure.
    """
    if state.error:
        return state  # abort if a prior node failed

    logger.info("classify_node_start", document_id=str(state.document_id))

    try:
        text = _get_classification_text(state)
        if not text.strip():
            raise ValueError("Document has no parseable text — cannot classify")

        client = get_llm_client()
        result, llm_response = _classify_with_llm(text, client)

        # Rule-based fallback if LLM is uncertain
        if result.confidence < LLM_CONFIDENCE_THRESHOLD:
            fallback = _classify_by_keywords(text)
            if fallback != DocumentType.UNKNOWN:
                logger.info(
                    "classify_using_keyword_fallback",
                    llm_type=result.doc_type,
                    llm_confidence=result.confidence,
                    fallback_type=fallback,
                )
                result = ClassificationResult(
                    doc_type=fallback,
                    confidence=0.75,
                    reasoning=f"Keyword fallback (LLM confidence was {result.confidence:.2f})",
                )

        state.classification = result

        # Persist LLM call record
        _save_llm_call(
            db, state.document_id, state.tenant_id,
            node="classify",
            prompt_version=CLASSIFY_PROMPT,
            llm_response=llm_response,
        )
        state.total_tokens += llm_response.total_tokens
        state.total_cost_usd += llm_response.cost_usd

        log_event_sync(
            db,
            tenant_id=state.tenant_id,
            document_id=state.document_id,
            event_type="document_classified",
            description=f"Classified as {result.doc_type} (confidence {result.confidence:.2f})",
            metadata={"doc_type": result.doc_type, "confidence": result.confidence},
        )
        db.commit()

        logger.info(
            "classify_node_complete",
            document_id=str(state.document_id),
            doc_type=result.doc_type,
            confidence=result.confidence,
        )

    except Exception as exc:
        logger.exception("classify_node_failed", document_id=str(state.document_id), error=str(exc))
        state.error = f"Classification failed: {exc}"
        state.failed_node = "classify"

    return state


def _get_classification_text(state: PipelineState) -> str:
    """Take the first N chars of page 1 (or full text if short)."""
    if not state.parsed_document or not state.parsed_document.pages:
        return ""
    first_page = state.parsed_document.pages[0].raw_text or ""
    # Use more pages if first page is very short (e.g. a cover sheet)
    if len(first_page) < 200 and len(state.parsed_document.pages) > 1:
        first_page += "\n" + state.parsed_document.pages[1].raw_text
    return first_page[:CLASSIFY_TEXT_LIMIT]


def _classify_with_llm(text: str, client: LLMClient) -> tuple[ClassificationResult, object]:
    """Call the LLM to classify the document. Returns (result, raw_llm_response)."""
    system_prompt = render_prompt(CLASSIFY_PROMPT, document_text=text)

    # We ask for JSON output; parse it manually since classification schema is tiny
    llm_response = client.complete(
        system="You are a logistics document classifier. Respond with valid JSON only.",
        user=system_prompt,
        temperature=0.0,
        max_tokens=256,
        trace_name="classify",
    )

    content = llm_response.content.strip()
    # Strip markdown code fences if the model adds them
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()

    data = json.loads(content)
    result = ClassificationResult(**data)
    return result, llm_response


def _classify_by_keywords(text: str) -> DocumentType:
    """
    Simple keyword voting — count matches per doc type.
    Returns the winning type or UNKNOWN if no clear winner.
    """
    text_lower = text.lower()
    scores: dict[str, int] = {t: 0 for t in KEYWORDS}
    for doc_type, keywords in KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                scores[doc_type] += 1

    best_type = max(scores, key=lambda t: scores[t])
    best_score = scores[best_type]

    # Require at least 2 keyword matches and a clear winner
    all_scores = list(scores.values())
    all_scores.sort(reverse=True)
    if best_score >= 2 and (len(all_scores) < 2 or all_scores[0] > all_scores[1]):
        return DocumentType(best_type)
    return DocumentType.UNKNOWN


def _save_llm_call(db, document_id, tenant_id, node, prompt_version, llm_response):
    call = LLMCall(
        document_id=document_id,
        tenant_id=tenant_id,
        pipeline_node=node,
        model=llm_response.model,
        prompt_version=prompt_version,
        trace_id=llm_response.trace_id,
        prompt_tokens=llm_response.prompt_tokens,
        completion_tokens=llm_response.completion_tokens,
        total_tokens=llm_response.total_tokens,
        cost_usd=llm_response.cost_usd,
        latency_ms=llm_response.latency_ms,
    )
    db.add(call)
