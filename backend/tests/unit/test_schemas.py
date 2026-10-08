"""Unit tests for extraction Pydantic schemas."""

import pytest
from pydantic import ValidationError

from app.schemas.extraction import (
    BBox,
    BillOfLadingSchema,
    ClassificationResult,
    CommercialInvoiceSchema,
    DocumentType,
    Evidence,
    ExtractedField,
    FieldStatus,
    LineItem,
    PackingListSchema,
    ef,
)


# ── ExtractedField ────────────────────────────────────────────────────────────

def test_extracted_field_defaults():
    f = ExtractedField()
    assert f.value is None
    assert f.confidence == 0.0
    assert f.status == FieldStatus.UNGROUNDED


def test_extracted_field_with_evidence():
    f = ExtractedField(
        value="MAERSK ELDORADO",
        confidence=0.95,
        evidence=Evidence(page=1, snippet="Vessel: MAERSK ELDORADO"),
        status=FieldStatus.GROUNDED,
    )
    assert f.value == "MAERSK ELDORADO"
    assert f.confidence == 0.95
    assert f.evidence.page == 1


def test_confidence_clamped():
    with pytest.raises(ValidationError):
        ExtractedField(confidence=1.5)
    with pytest.raises(ValidationError):
        ExtractedField(confidence=-0.1)


def test_bbox_x1_must_be_gte_x0():
    with pytest.raises(ValidationError):
        BBox(x0=100, y0=100, x1=50, y1=200)  # x1 < x0


def test_ef_shorthand():
    f = ef("SHANGHAI", 0.9, page=1, snippet="Port of Loading: SHANGHAI")
    assert f.value == "SHANGHAI"
    assert f.confidence == 0.9
    assert f.evidence.snippet == "Port of Loading: SHANGHAI"


# ── BillOfLadingSchema ────────────────────────────────────────────────────────

def test_bol_schema_all_optional():
    """All fields optional — an empty schema is valid (LLM may find nothing)."""
    bol = BillOfLadingSchema()
    assert bol.bl_number is None
    assert bol.vessel is None


def test_bol_schema_with_data():
    bol = BillOfLadingSchema(
        bl_number=ef("BL2024001234", 0.98, 1, "B/L Number: BL2024001234"),
        vessel=ef("MSC AURORA", 0.97, 1, "Vessel: MSC AURORA"),
        gross_weight=ef("15250.5", 0.92, 2, "Gross Weight: 15250.5"),
        weight_unit=ef("KG", 0.99, 2, "15250.5 KG"),
        container_numbers=[
            ef("MSCU1234560", 0.95, 2, "MSCU1234560"),
            ef("TEMU7654321", 0.94, 2, "TEMU7654321"),
        ],
    )
    assert bol.bl_number.value == "BL2024001234"
    assert len(bol.container_numbers) == 2
    assert bol.container_numbers[0].value == "MSCU1234560"


# ── CommercialInvoiceSchema ───────────────────────────────────────────────────

def test_invoice_schema_with_line_items():
    inv = CommercialInvoiceSchema(
        invoice_number=ef("INV-2024001", 0.99, 1, "Invoice No.: INV-2024001"),
        currency=ef("USD", 0.99, 1, "Currency: USD"),
        total_amount=ef("15250.00", 0.95, 2, "TOTAL: USD 15,250.00"),
        line_items=[
            LineItem(
                description=ef("Electronic Module", 0.9, 2, "Electronic Module"),
                quantity=ef("100", 0.9, 2, "100"),
                unit_price=ef("152.50", 0.9, 2, "152.50"),
                line_total=ef("15250.00", 0.9, 2, "15,250.00"),
            )
        ],
    )
    assert inv.invoice_number.value == "INV-2024001"
    assert len(inv.line_items) == 1
    assert inv.line_items[0].quantity.value == "100"


# ── PackingListSchema ─────────────────────────────────────────────────────────

def test_packing_list_schema():
    pl = PackingListSchema(
        packing_list_number=ef("PL-2024001", 0.98, 1, "Packing List No.: PL-2024001"),
        total_packages=ef("5", 0.99, 2, "Total Pkgs: 5"),
        total_gross_weight=ef("1250.5", 0.95, 2, "Total Gross: 1250.5 kg"),
    )
    assert pl.total_packages.value == "5"


# ── ClassificationResult ──────────────────────────────────────────────────────

def test_classification_result():
    result = ClassificationResult(
        doc_type=DocumentType.BILL_OF_LADING,
        confidence=0.97,
        reasoning="Document contains vessel name, container numbers, and ports.",
    )
    assert result.doc_type == "bill_of_lading"
    assert result.confidence == 0.97


def test_classification_unknown():
    result = ClassificationResult(
        doc_type=DocumentType.UNKNOWN,
        confidence=0.3,
        reasoning="Could not determine document type from text.",
    )
    assert result.doc_type == "unknown"
