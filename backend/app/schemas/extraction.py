"""
Pydantic schemas for LLM extraction output.

Design principles:
- Every extracted value is wrapped in ExtractedField, which carries confidence
  and an evidence reference (page + bbox + snippet). This is what drives evidence
  highlighting in the UI.
- The LLM returns these schemas directly via structured output / tool calls.
  Pydantic validates the response immediately after the LLM call.
- Optional fields use Optional[ExtractedField] so the LLM can explicitly return
  null rather than omitting the field (makes missing-field detection reliable).
- We keep a separate "Evidence" model because it's also stored in the DB and
  referenced by the grounding node.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ── Evidence reference ────────────────────────────────────────────────────────

class BBox(BaseModel):
    """Bounding box in page coordinates (points from top-left)."""
    x0: float
    y0: float
    x1: float
    y1: float

    @field_validator("x1")
    @classmethod
    def x1_gt_x0(cls, v: float, info) -> float:
        if "x0" in info.data and v < info.data["x0"]:
            raise ValueError("x1 must be >= x0")
        return v

    @field_validator("y1")
    @classmethod
    def y1_gt_y0(cls, v: float, info) -> float:
        if "y0" in info.data and v < info.data["y0"]:
            raise ValueError("y1 must be >= y0")
        return v


class Evidence(BaseModel):
    """
    Where in the document a field value came from.
    The snippet is the exact text the LLM cited; bbox is computed by the
    grounding node by matching snippet text to parsed words.
    """
    page: int = Field(ge=1, description="1-indexed page number")
    snippet: str = Field(description="Exact text from the document supporting this value")
    bbox: Optional[BBox] = Field(
        default=None,
        description="Bounding box — filled in by the grounding node, not the LLM"
    )


class FieldStatus(str, Enum):
    GROUNDED = "grounded"
    UNGROUNDED = "ungrounded"
    CORRECTED = "corrected"


# ── Core field wrapper ────────────────────────────────────────────────────────

class ExtractedField(BaseModel):
    """
    Wrapper around every extracted value.
    The LLM fills value, confidence, and evidence.snippet.
    The grounding node fills evidence.bbox and may adjust confidence.
    """
    value: Optional[str] = Field(
        default=None,
        description="Extracted value as a string. null if not found in document."
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="LLM confidence 0–1. Lowered if grounding fails."
    )
    evidence: Optional[Evidence] = Field(
        default=None,
        description="Source location in the document. null if field not found."
    )
    status: FieldStatus = Field(
        default=FieldStatus.UNGROUNDED,
        description="Set by the grounding node."
    )

    @field_validator("evidence", mode="before")
    @classmethod
    def require_snippet_if_value(cls, v, info):
        """If a value was extracted, there must be a snippet."""
        # We don't hard-fail here — grounding node will mark it ungrounded
        return v


def ef(value: str | None, confidence: float, page: int, snippet: str) -> ExtractedField:
    """Shorthand constructor used in tests and the synthetic data generator."""
    return ExtractedField(
        value=value,
        confidence=confidence,
        evidence=Evidence(page=page, snippet=snippet) if snippet else None,
    )


# ── Document-type schemas ─────────────────────────────────────────────────────

class PartyInfo(BaseModel):
    """Represents a named party (shipper, consignee, etc.)."""
    name: Optional[ExtractedField] = None
    address: Optional[ExtractedField] = None


class BillOfLadingSchema(BaseModel):
    """
    Extraction schema for a Bill of Lading.
    All fields are optional because the LLM must return null rather than guess.
    """
    bl_number: Optional[ExtractedField] = None
    shipper: Optional[PartyInfo] = None
    consignee: Optional[PartyInfo] = None
    notify_party: Optional[PartyInfo] = None
    vessel: Optional[ExtractedField] = None
    voyage: Optional[ExtractedField] = None
    port_of_loading: Optional[ExtractedField] = None
    port_of_discharge: Optional[ExtractedField] = None
    container_numbers: Optional[list[ExtractedField]] = None
    seal_numbers: Optional[list[ExtractedField]] = None
    gross_weight: Optional[ExtractedField] = None
    weight_unit: Optional[ExtractedField] = None
    packages_count: Optional[ExtractedField] = None
    goods_description: Optional[ExtractedField] = None
    freight_terms: Optional[ExtractedField] = None
    date_of_issue: Optional[ExtractedField] = None
    incoterm: Optional[ExtractedField] = None


class LineItem(BaseModel):
    """One line item on a commercial invoice."""
    description: Optional[ExtractedField] = None
    hs_code: Optional[ExtractedField] = None
    quantity: Optional[ExtractedField] = None
    unit_price: Optional[ExtractedField] = None
    line_total: Optional[ExtractedField] = None


class CommercialInvoiceSchema(BaseModel):
    """Extraction schema for a Commercial Invoice."""
    invoice_number: Optional[ExtractedField] = None
    invoice_date: Optional[ExtractedField] = None
    seller: Optional[PartyInfo] = None
    buyer: Optional[PartyInfo] = None
    currency: Optional[ExtractedField] = None
    line_items: Optional[list[LineItem]] = None
    subtotal: Optional[ExtractedField] = None
    tax_or_freight: Optional[ExtractedField] = None
    total_amount: Optional[ExtractedField] = None
    incoterm: Optional[ExtractedField] = None
    payment_terms: Optional[ExtractedField] = None
    po_reference: Optional[ExtractedField] = None


class PackageItem(BaseModel):
    """One package/carton on a packing list."""
    package_id: Optional[ExtractedField] = None
    description: Optional[ExtractedField] = None
    quantity: Optional[ExtractedField] = None
    net_weight: Optional[ExtractedField] = None
    gross_weight: Optional[ExtractedField] = None
    dimensions: Optional[ExtractedField] = None


class PackingListSchema(BaseModel):
    """Extraction schema for a Packing List."""
    packing_list_number: Optional[ExtractedField] = None
    date: Optional[ExtractedField] = None
    shipper: Optional[PartyInfo] = None
    consignee: Optional[PartyInfo] = None
    packages: Optional[list[PackageItem]] = None
    total_packages: Optional[ExtractedField] = None
    total_gross_weight: Optional[ExtractedField] = None
    total_net_weight: Optional[ExtractedField] = None


# ── Classification schema ─────────────────────────────────────────────────────

class DocumentType(str, Enum):
    BILL_OF_LADING = "bill_of_lading"
    COMMERCIAL_INVOICE = "commercial_invoice"
    PACKING_LIST = "packing_list"
    UNKNOWN = "unknown"


class ClassificationResult(BaseModel):
    """LLM response from the classify node."""
    doc_type: DocumentType
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str

    model_config = {"use_enum_values": True}


# ── Union type for the pipeline state ────────────────────────────────────────

ExtractionSchema = BillOfLadingSchema | CommercialInvoiceSchema | PackingListSchema

SCHEMA_BY_TYPE: dict[str, type] = {
    DocumentType.BILL_OF_LADING: BillOfLadingSchema,
    DocumentType.COMMERCIAL_INVOICE: CommercialInvoiceSchema,
    DocumentType.PACKING_LIST: PackingListSchema,
}
