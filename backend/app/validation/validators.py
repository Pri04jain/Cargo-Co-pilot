"""
Deterministic document validators.

Each validator takes a Pydantic extraction schema and returns a list of
CheckResults. No LLM is involved — these are pure Python logic checks.

Categories:
  1. Required field checks — is a mandatory field present and non-empty?
  2. Format checks — is the value in the right format (date, currency, etc.)?
  3. Math checks — do line items sum to the invoice total?

The validate node calls the matching validator for each document type.
"""

from typing import Optional

from app.schemas.extraction import (
    BillOfLadingSchema,
    CommercialInvoiceSchema,
    ExtractedField,
    PackingListSchema,
)
from app.validation.formats import (
    validate_container_number,
    validate_currency_code,
    validate_date,
    validate_hs_code,
    validate_numeric,
)
from app.validation.types import CheckResult, Severity, fail_check, pass_check

# Tolerance for floating-point math comparisons (1 cent)
MATH_TOLERANCE = 0.02


def _val(field: Optional[ExtractedField]) -> Optional[str]:
    """Safely extract the value from an ExtractedField, or None."""
    return field.value if field and field.value else None


# ── Bill of Lading validators ─────────────────────────────────────────────────

REQUIRED_BOL_FIELDS = [
    ("bl_number", "B/L Number"),
    ("shipper", "Shipper"),
    ("consignee", "Consignee"),
    ("port_of_loading", "Port of Loading"),
    ("port_of_discharge", "Port of Discharge"),
    ("gross_weight", "Gross Weight"),
    ("date_of_issue", "Date of Issue"),
]


def validate_bill_of_lading(schema: BillOfLadingSchema) -> list[CheckResult]:
    results: list[CheckResult] = []

    # ── Required fields ───────────────────────────────────────────────────────
    for attr, label in REQUIRED_BOL_FIELDS:
        # Handle nested party objects (shipper, consignee)
        if attr in ("shipper", "consignee"):
            party = getattr(schema, attr, None)
            name_val = _val(party.name) if party else None
            if not name_val:
                results.append(fail_check(
                    f"required_{attr}",
                    f"{label} name is missing or empty",
                    Severity.ERROR,
                    field_name=f"{attr}.name",
                ))
            else:
                results.append(pass_check(f"required_{attr}", f"{label} present", f"{attr}.name"))
        else:
            field = getattr(schema, attr, None)
            val = _val(field)
            if not val:
                results.append(fail_check(
                    f"required_{attr}",
                    f"{label} is missing or empty",
                    Severity.ERROR,
                    field_name=attr,
                ))
            else:
                results.append(pass_check(f"required_{attr}", f"{label} present", attr))

    # ── Container number format (ISO 6346) ────────────────────────────────────
    if schema.container_numbers:
        for i, cn_field in enumerate(schema.container_numbers):
            cn = _val(cn_field)
            if cn:
                cn_clean = cn.upper().replace(" ", "")
                if not validate_container_number(cn_clean):
                    results.append(fail_check(
                        f"container_format_{i}",
                        f"Container number '{cn}' does not match ISO 6346 format or has invalid check digit",
                        Severity.WARNING,
                        field_name=f"container_numbers[{i}]",
                    ))
                else:
                    results.append(pass_check(
                        f"container_format_{i}",
                        f"Container {cn} is valid ISO 6346",
                        f"container_numbers[{i}]",
                    ))
    else:
        results.append(fail_check(
            "required_container_numbers",
            "No container numbers found",
            Severity.WARNING,
            field_name="container_numbers",
        ))

    # ── Date format ───────────────────────────────────────────────────────────
    doi = _val(schema.date_of_issue)
    if doi and not validate_date(doi):
        results.append(fail_check(
            "date_of_issue_format",
            f"Date of issue '{doi}' is not a recognisable date format",
            Severity.WARNING,
            field_name="date_of_issue",
        ))

    # ── Gross weight is numeric ───────────────────────────────────────────────
    gw = _val(schema.gross_weight)
    if gw:
        parsed_gw = validate_numeric(gw)
        if parsed_gw is None:
            results.append(fail_check(
                "gross_weight_numeric",
                f"Gross weight '{gw}' is not a valid number",
                Severity.ERROR,
                field_name="gross_weight",
            ))
        elif parsed_gw <= 0:
            results.append(fail_check(
                "gross_weight_positive",
                f"Gross weight must be positive, got {parsed_gw}",
                Severity.ERROR,
                field_name="gross_weight",
            ))

    return results


# ── Commercial Invoice validators ─────────────────────────────────────────────

REQUIRED_INVOICE_FIELDS = [
    ("invoice_number", "Invoice Number"),
    ("invoice_date", "Invoice Date"),
    ("seller", "Seller"),
    ("buyer", "Buyer"),
    ("currency", "Currency"),
    ("total_amount", "Total Amount"),
]


def validate_commercial_invoice(schema: CommercialInvoiceSchema) -> list[CheckResult]:
    results: list[CheckResult] = []

    # ── Required fields ───────────────────────────────────────────────────────
    for attr, label in REQUIRED_INVOICE_FIELDS:
        if attr in ("seller", "buyer"):
            party = getattr(schema, attr, None)
            name_val = _val(party.name) if party else None
            if not name_val:
                results.append(fail_check(
                    f"required_{attr}",
                    f"{label} name is missing",
                    Severity.ERROR,
                    field_name=f"{attr}.name",
                ))
            else:
                results.append(pass_check(f"required_{attr}", f"{label} present", f"{attr}.name"))
        else:
            field = getattr(schema, attr, None)
            val = _val(field)
            if not val:
                results.append(fail_check(
                    f"required_{attr}",
                    f"{label} is missing or empty",
                    Severity.ERROR,
                    field_name=attr,
                ))
            else:
                results.append(pass_check(f"required_{attr}", f"{label} present", attr))

    # ── Currency code format ──────────────────────────────────────────────────
    currency = _val(schema.currency)
    if currency:
        if not validate_currency_code(currency):
            results.append(fail_check(
                "currency_code_format",
                f"Currency code '{currency}' is not a recognised ISO 4217 code",
                Severity.ERROR,
                field_name="currency",
            ))

    # ── Invoice date format ───────────────────────────────────────────────────
    inv_date = _val(schema.invoice_date)
    if inv_date and not validate_date(inv_date):
        results.append(fail_check(
            "invoice_date_format",
            f"Invoice date '{inv_date}' is not a recognisable date format",
            Severity.WARNING,
            field_name="invoice_date",
        ))

    # ── Line items: HS code format ────────────────────────────────────────────
    if schema.line_items:
        for i, item in enumerate(schema.line_items):
            hs = _val(item.hs_code)
            if hs and not validate_hs_code(hs):
                results.append(fail_check(
                    f"hs_code_format_{i}",
                    f"HS code '{hs}' on line {i+1} must have at least 6 digits",
                    Severity.WARNING,
                    field_name=f"line_items[{i}].hs_code",
                ))

    # ── Math check: line item totals sum to subtotal ──────────────────────────
    results.extend(_check_invoice_math(schema))

    return results


def _check_invoice_math(schema: CommercialInvoiceSchema) -> list[CheckResult]:
    """
    Verify: sum(line_item.line_total) ≈ subtotal
    And: subtotal + tax_or_freight ≈ total_amount

    We use MATH_TOLERANCE (2 cents) to allow for rounding differences.
    If currencies differ between line items and total, we skip the check
    (multi-currency invoices are valid but not math-checkable).
    """
    results = []

    total_str = _val(schema.total_amount)
    subtotal_str = _val(schema.subtotal)
    freight_str = _val(schema.tax_or_freight)

    total = validate_numeric(total_str) if total_str else None
    subtotal = validate_numeric(subtotal_str) if subtotal_str else None
    freight = validate_numeric(freight_str) if freight_str else 0.0

    if not schema.line_items:
        return [fail_check(
            "line_items_present",
            "Invoice has no line items",
            Severity.ERROR,
            field_name="line_items",
        )]

    # Sum up line item totals
    computed_subtotal = 0.0
    all_numeric = True
    for i, item in enumerate(schema.line_items):
        lt = validate_numeric(_val(item.line_total))
        if lt is None:
            all_numeric = False
            results.append(fail_check(
                f"line_total_numeric_{i}",
                f"Line item {i+1} total is not a valid number: '{_val(item.line_total)}'",
                Severity.WARNING,
                field_name=f"line_items[{i}].line_total",
            ))
        else:
            computed_subtotal += lt

    if not all_numeric:
        return results

    computed_subtotal = round(computed_subtotal, 2)

    # Check subtotal matches sum of line items
    if subtotal is not None:
        diff = abs(computed_subtotal - subtotal)
        if diff > MATH_TOLERANCE:
            results.append(fail_check(
                "line_items_sum_to_subtotal",
                f"Line items sum to {computed_subtotal:.2f} but subtotal is {subtotal:.2f} "
                f"(difference: {diff:.2f})",
                Severity.ERROR,
                field_name="subtotal",
            ))
        else:
            results.append(pass_check(
                "line_items_sum_to_subtotal",
                f"Line items correctly sum to subtotal {subtotal:.2f}",
            ))

    # Check subtotal + freight ≈ total
    if total is not None and subtotal is not None:
        computed_total = round(subtotal + freight, 2)
        diff = abs(computed_total - total)
        if diff > MATH_TOLERANCE:
            results.append(fail_check(
                "subtotal_plus_freight_equals_total",
                f"Subtotal ({subtotal:.2f}) + freight ({freight:.2f}) = {computed_total:.2f} "
                f"but invoice total is {total:.2f} (difference: {diff:.2f})",
                Severity.ERROR,
                field_name="total_amount",
            ))
        else:
            results.append(pass_check(
                "subtotal_plus_freight_equals_total",
                f"Invoice total {total:.2f} matches subtotal + freight",
            ))

    return results


# ── Packing List validators ───────────────────────────────────────────────────

REQUIRED_PL_FIELDS = [
    ("packing_list_number", "Packing List Number"),
    ("date", "Date"),
    ("shipper", "Shipper"),
    ("consignee", "Consignee"),
    ("total_packages", "Total Packages"),
    ("total_gross_weight", "Total Gross Weight"),
]


def validate_packing_list(schema: PackingListSchema) -> list[CheckResult]:
    results: list[CheckResult] = []

    # ── Required fields ───────────────────────────────────────────────────────
    for attr, label in REQUIRED_PL_FIELDS:
        if attr in ("shipper", "consignee"):
            party = getattr(schema, attr, None)
            name_val = _val(party.name) if party else None
            if not name_val:
                results.append(fail_check(
                    f"required_{attr}",
                    f"{label} name is missing",
                    Severity.ERROR,
                    field_name=f"{attr}.name",
                ))
            else:
                results.append(pass_check(f"required_{attr}", f"{label} present", f"{attr}.name"))
        else:
            field = getattr(schema, attr, None)
            val = _val(field)
            if not val:
                results.append(fail_check(
                    f"required_{attr}",
                    f"{label} is missing or empty",
                    Severity.ERROR,
                    field_name=attr,
                ))
            else:
                results.append(pass_check(f"required_{attr}", f"{label} present", attr))

    # ── Date format ───────────────────────────────────────────────────────────
    pl_date = _val(schema.date)
    if pl_date and not validate_date(pl_date):
        results.append(fail_check(
            "date_format",
            f"Packing list date '{pl_date}' is not a recognisable date format",
            Severity.WARNING,
            field_name="date",
        ))

    # ── Math: package gross weights sum to total_gross_weight ─────────────────
    if schema.packages:
        computed_gross = 0.0
        all_numeric = True
        for i, pkg in enumerate(schema.packages):
            gw = validate_numeric(_val(pkg.gross_weight))
            if gw is None:
                all_numeric = False
            else:
                computed_gross += gw

        if all_numeric:
            total_gross_str = _val(schema.total_gross_weight)
            total_gross = validate_numeric(total_gross_str) if total_gross_str else None
            if total_gross is not None:
                diff = abs(round(computed_gross, 1) - total_gross)
                if diff > 0.5:  # 0.5 kg tolerance (rounding in packing lists)
                    results.append(fail_check(
                        "package_weights_sum_to_total",
                        f"Package gross weights sum to {computed_gross:.1f} kg but total is {total_gross:.1f} kg "
                        f"(difference: {diff:.1f} kg)",
                        Severity.ERROR,
                        field_name="total_gross_weight",
                    ))
                else:
                    results.append(pass_check(
                        "package_weights_sum_to_total",
                        f"Package weights correctly sum to {total_gross:.1f} kg",
                    ))

    return results
