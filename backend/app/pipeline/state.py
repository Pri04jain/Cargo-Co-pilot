"""
LangGraph pipeline state.

All pipeline nodes read from and write to this typed state object.
Using a single typed state (rather than passing dicts) means:
  - Each node's inputs/outputs are explicit and IDE-checked
  - Unit testing a node is straightforward: build a state, call the node, assert on the output
  - LangGraph can serialize state between retries

Design note: we use a dataclass (not Pydantic) because LangGraph's StateGraph
works with any class that can be serialized; a dataclass is lighter weight
here since the state lives only in memory during a single pipeline run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

from app.parsers.types import ParsedDocument
from app.schemas.extraction import (
    ClassificationResult,
    CommercialInvoiceSchema,
    BillOfLadingSchema,
    PackingListSchema,
)


@dataclass
class PipelineState:
    """
    Mutable state passed between all pipeline nodes.

    Naming convention:
      - Input fields (set at start): document_id, tenant_id, storage_path
      - Node outputs: each node writes its own fields
      - error: any node can set this to abort the pipeline
    """

    # ── Inputs (set by the ingest node) ──────────────────────────────────────
    document_id: uuid.UUID = field(default_factory=uuid.uuid4)
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    storage_path: str = ""
    mime_type: str = ""

    # ── Parse node output ─────────────────────────────────────────────────────
    parsed_document: Optional[ParsedDocument] = None

    # ── Classify node output ──────────────────────────────────────────────────
    classification: Optional[ClassificationResult] = None

    # ── Extract node output ───────────────────────────────────────────────────
    # One of these will be set depending on doc_type
    extraction_bol: Optional[BillOfLadingSchema] = None
    extraction_invoice: Optional[CommercialInvoiceSchema] = None
    extraction_packing: Optional[PackingListSchema] = None
    extraction_attempt: int = 0       # 0 = initial, 1-2 = retries

    # ── Validate node output ──────────────────────────────────────────────────
    validation_errors: list[dict] = field(default_factory=list)    # severity=error
    validation_warnings: list[dict] = field(default_factory=list)  # severity=warning
    validation_infos: list[dict] = field(default_factory=list)     # severity=info

    # Fields that need re-extraction (used by retry_extract node)
    fields_to_retry: list[dict] = field(default_factory=list)

    # ── Route node output ─────────────────────────────────────────────────────
    routing_decision: Optional[str] = None   # auto_approve | manager_approval | exception_queue
    routing_rule_id: Optional[str] = None
    routing_reason: Optional[str] = None

    # ── Aggregate metrics (filled by finalize node) ───────────────────────────
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    overall_confidence: float = 0.0
    grounding_rate: float = 0.0

    # ── Error handling ────────────────────────────────────────────────────────
    error: Optional[str] = None  # if set, finalize marks document as failed
    failed_node: Optional[str] = None
