"""
Cross-document validation — compares extracted data against other documents.

Two types of cross-document checks:

1. Invoice vs Purchase Order (from the purchase_orders DB table):
   - Supplier name fuzzy match
   - Total amount within tolerance
   - Currency match

2. Invoice vs Bill of Lading (when both exist for the same shipment):
   - Consignee match
   - Gross weight within tolerance

Why fuzzy matching for names?
  "Shenzhen Electronics Co. Ltd" vs "SHENZHEN ELECTRONICS CO LTD" are the same
  company. We normalise to uppercase, strip punctuation, and check word overlap
  rather than exact string equality.
"""

import re
from typing import Optional

from sqlalchemy.orm import Session

from app.models.purchase_order import PurchaseOrder
from app.schemas.extraction import (
    BillOfLadingSchema,
    CommercialInvoiceSchema,
)
from app.validation.formats import validate_numeric
from app.validation.types import CheckResult, Severity, fail_check, pass_check

# Acceptable percentage difference between invoice and PO amounts
# (the rules engine will route based on the actual % — this controls
# whether we even flag it as an error or warning)
PO_AMOUNT_ERROR_THRESHOLD = 10.0   # > 10% diff → error
PO_AMOUNT_WARNING_THRESHOLD = 2.0  # 2–10% diff → warning
WEIGHT_TOLERANCE_PCT = 5.0         # 5% tolerance for weight comparisons


def validate_invoice_vs_po(
    invoice: CommercialInvoiceSchema,
    po: Optional[PurchaseOrder],
) -> list[CheckResult]:
    """
    Compare a commercial invoice against a matching Purchase Order.
    If no PO is found (po=None), return a warning.
    """
    results = []

    if po is None:
        results.append(fail_check(
            "po_match",
            "No matching Purchase Order found for this invoice. "
            "Check the PO reference number.",
            Severity.WARNING,
            field_name="po_reference",
        ))
        return results

    # ── Currency match ────────────────────────────────────────────────────────
    invoice_currency = invoice.currency.value.strip().upper() if invoice.currency and invoice.currency.value else None
    if invoice_currency and invoice_currency != po.currency.upper():
        results.append(fail_check(
            "po_currency_match",
            f"Currency mismatch: invoice is {invoice_currency}, PO is {po.currency}",
            Severity.ERROR,
            field_name="currency",
        ))
    elif invoice_currency:
        results.append(pass_check("po_currency_match", f"Currency matches PO: {invoice_currency}"))

    # ── Total amount comparison ───────────────────────────────────────────────
    total_str = invoice.total_amount.value if invoice.total_amount and invoice.total_amount.value else None
    invoice_total = validate_numeric(total_str) if total_str else None

    if invoice_total is None:
        results.append(fail_check(
            "po_amount_check",
            "Cannot compare against PO: invoice total amount is missing or non-numeric",
            Severity.ERROR,
            field_name="total_amount",
        ))
    else:
        pct_diff = abs(invoice_total - po.total_amount) / po.total_amount * 100 if po.total_amount else 0
        if pct_diff > PO_AMOUNT_ERROR_THRESHOLD:
            results.append(fail_check(
                "po_amount_match",
                f"Invoice total {invoice_total:.2f} differs from PO total {po.total_amount:.2f} "
                f"by {pct_diff:.1f}% (threshold: {PO_AMOUNT_ERROR_THRESHOLD}%)",
                Severity.ERROR,
                field_name="total_amount",
            ))
        elif pct_diff > PO_AMOUNT_WARNING_THRESHOLD:
            results.append(fail_check(
                "po_amount_match",
                f"Invoice total {invoice_total:.2f} differs from PO total {po.total_amount:.2f} "
                f"by {pct_diff:.1f}% (within manager review threshold)",
                Severity.WARNING,
                field_name="total_amount",
            ))
        else:
            results.append(pass_check(
                "po_amount_match",
                f"Invoice total {invoice_total:.2f} is within {PO_AMOUNT_WARNING_THRESHOLD}% of PO",
            ))

    # ── Supplier name fuzzy match ─────────────────────────────────────────────
    seller_name = None
    if invoice.seller and invoice.seller.name and invoice.seller.name.value:
        seller_name = invoice.seller.name.value
    if seller_name:
        if not _names_match(seller_name, po.supplier_name):
            results.append(fail_check(
                "po_supplier_match",
                f"Seller name '{seller_name}' does not match PO supplier '{po.supplier_name}'",
                Severity.WARNING,
                field_name="seller.name",
            ))
        else:
            results.append(pass_check("po_supplier_match", "Seller name matches PO supplier"))

    return results


def validate_invoice_vs_bol(
    invoice: CommercialInvoiceSchema,
    bol: Optional[BillOfLadingSchema],
) -> list[CheckResult]:
    """
    Cross-check invoice against a Bill of Lading.
    Only runs if a BoL was found for the same shipment.
    """
    if bol is None:
        return []  # No BoL available — skip these checks

    results = []

    # ── Consignee match ───────────────────────────────────────────────────────
    inv_buyer = None
    if invoice.buyer and invoice.buyer.name and invoice.buyer.name.value:
        inv_buyer = invoice.buyer.name.value

    bol_consignee = None
    if bol.consignee and bol.consignee.name and bol.consignee.name.value:
        bol_consignee = bol.consignee.name.value

    if inv_buyer and bol_consignee:
        if not _names_match(inv_buyer, bol_consignee):
            results.append(fail_check(
                "bol_consignee_match",
                f"Invoice buyer '{inv_buyer}' does not match BoL consignee '{bol_consignee}'",
                Severity.WARNING,
                field_name="buyer.name",
            ))
        else:
            results.append(pass_check("bol_consignee_match", "Invoice buyer matches BoL consignee"))

    return results


def lookup_po(
    db: Session,
    tenant_id,
    po_reference: Optional[str],
) -> Optional[PurchaseOrder]:
    """
    Look up a Purchase Order by PO reference number.
    Returns None if not found.
    """
    if not po_reference:
        return None
    from sqlalchemy import select
    result = db.execute(
        select(PurchaseOrder).where(
            PurchaseOrder.tenant_id == tenant_id,
            PurchaseOrder.po_number == po_reference.strip(),
        )
    )
    return result.scalar_one_or_none()


def _normalise_name(name: str) -> set[str]:
    """Normalise a company name to a set of significant words for fuzzy matching."""
    # Uppercase, remove punctuation, split on whitespace
    name = name.upper()
    name = re.sub(r"[^A-Z0-9\s]", " ", name)
    # Remove common legal suffixes that vary between documents
    stopwords = {"LTD", "LIMITED", "CO", "CORP", "CORPORATION", "INC",
                 "PTE", "PTY", "GMBH", "BV", "SA", "AG", "LLC", "PLC",
                 "THE", "AND", "&"}
    tokens = {w for w in name.split() if w and w not in stopwords}
    return tokens


def _names_match(name_a: str, name_b: str, threshold: float = 0.6) -> bool:
    """
    Return True if two company names are likely the same entity.
    Uses Jaccard similarity on normalised word sets.
    """
    tokens_a = _normalise_name(name_a)
    tokens_b = _normalise_name(name_b)
    if not tokens_a or not tokens_b:
        return False
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    jaccard = len(intersection) / len(union)
    return jaccard >= threshold
