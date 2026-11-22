"""
Unit tests for validators — no external dependencies needed.

These are pure Python functions, so they run locally without Docker.
Every validator and format check gets at least one pass and one fail test.
"""

import pytest

from app.validation.formats import (
    validate_container_number,
    validate_currency_code,
    validate_date,
    validate_hs_code,
    validate_numeric,
)
from app.validation.types import CheckStatus, Severity


# ── ISO 6346 Container number ─────────────────────────────────────────────────

class TestContainerNumber:
    def test_valid_container(self):
        # Use a hand-computed valid ISO 6346 number
        # MSCU1234565 — check digit verified against the algorithm
        # We'll use a known valid one: TEMU0078913
        # For tests, we verify our algorithm is self-consistent
        from app.validation.formats import _CHAR_VALUES

        # Build a number and compute its check digit
        prefix = "MSCU"
        serial = "123456"
        chars = prefix + serial

        total = 0
        for i, ch in enumerate(chars):
            if ch.isalpha():
                val = _CHAR_VALUES[ch]
            else:
                val = int(ch)
            total += val * (2 ** i)

        remainder = total % 11
        check = 0 if remainder == 10 else remainder
        container = f"{prefix}{serial}{check}"

        assert validate_container_number(container) is True

    def test_wrong_check_digit(self):
        # Build a valid number then corrupt the check digit
        from app.validation.formats import _CHAR_VALUES
        prefix = "MSCU"
        serial = "123456"
        chars = prefix + serial
        total = sum(
            (_CHAR_VALUES[c] if c.isalpha() else int(c)) * (2 ** i)
            for i, c in enumerate(chars)
        )
        remainder = total % 11
        correct = 0 if remainder == 10 else remainder
        wrong = (correct + 1) % 10  # use a different digit

        container = f"{prefix}{serial}{wrong}"
        assert validate_container_number(container) is False

    def test_wrong_format_too_short(self):
        assert validate_container_number("MSCU12345") is False

    def test_wrong_format_numeric_prefix(self):
        assert validate_container_number("1234567890A") is False

    def test_lowercase_accepted(self):
        # validate_container_number uppercases internally
        from app.validation.formats import _CHAR_VALUES
        prefix = "mscu"
        serial = "123456"
        chars = prefix.upper() + serial
        total = sum(
            (_CHAR_VALUES[c] if c.isalpha() else int(c)) * (2 ** i)
            for i, c in enumerate(chars)
        )
        remainder = total % 11
        check = 0 if remainder == 10 else remainder
        container = f"{prefix}{serial}{check}"
        assert validate_container_number(container) is True


# ── Date formats ──────────────────────────────────────────────────────────────

class TestDateValidation:
    def test_iso_format(self):
        assert validate_date("2024-03-15") is True

    def test_european_format(self):
        assert validate_date("15/03/2024") is True

    def test_us_format(self):
        assert validate_date("03/15/2024") is True

    def test_text_format(self):
        assert validate_date("15 Mar 2024") is True
        assert validate_date("March 15, 2024") is True

    def test_dot_format(self):
        assert validate_date("15.03.2024") is True

    def test_invalid_date(self):
        assert validate_date("not a date") is False
        assert validate_date("2024-13-01") is False  # month 13
        assert validate_date("") is False


# ── Currency codes ────────────────────────────────────────────────────────────

class TestCurrencyCode:
    def test_valid_codes(self):
        for code in ["USD", "EUR", "GBP", "CNY", "JPY"]:
            assert validate_currency_code(code) is True

    def test_invalid_code(self):
        assert validate_currency_code("XYZ") is False
        assert validate_currency_code("US") is False
        assert validate_currency_code("USDD") is False

    def test_lowercase_accepted(self):
        assert validate_currency_code("usd") is True


# ── HS codes ──────────────────────────────────────────────────────────────────

class TestHSCode:
    def test_valid_with_dot(self):
        assert validate_hs_code("8471.30") is True

    def test_valid_8_digits(self):
        assert validate_hs_code("84713000") is True

    def test_too_short(self):
        assert validate_hs_code("8471") is False

    def test_non_numeric(self):
        assert validate_hs_code("ABC123") is False


# ── Numeric parsing ───────────────────────────────────────────────────────────

class TestNumericParsing:
    def test_plain_number(self):
        assert validate_numeric("15250.00") == pytest.approx(15250.00)

    def test_with_comma(self):
        assert validate_numeric("15,250.00") == pytest.approx(15250.00)

    def test_integer(self):
        assert validate_numeric("500") == pytest.approx(500.0)

    def test_invalid(self):
        assert validate_numeric("not a number") is None
        assert validate_numeric("") is None
        assert validate_numeric(None) is None


# ── Invoice math validator ────────────────────────────────────────────────────

class TestInvoiceMath:
    def _make_invoice(self, line_totals, subtotal, freight, total):
        from app.schemas.extraction import (
            CommercialInvoiceSchema, ExtractedField, Evidence, LineItem, PartyInfo
        )

        def ef(v):
            return ExtractedField(value=str(v), confidence=0.9,
                                   evidence=Evidence(page=1, snippet=str(v)))

        items = [
            LineItem(line_total=ef(lt))
            for lt in line_totals
        ]
        return CommercialInvoiceSchema(
            invoice_number=ef("INV-001"),
            invoice_date=ef("2024-01-15"),
            seller=PartyInfo(name=ef("Seller Co")),
            buyer=PartyInfo(name=ef("Buyer Co")),
            currency=ef("USD"),
            line_items=items,
            subtotal=ef(subtotal),
            tax_or_freight=ef(freight),
            total_amount=ef(total),
        )

    def test_correct_math_passes(self):
        from app.validation.validators import validate_commercial_invoice
        schema = self._make_invoice(
            line_totals=[100.0, 200.0, 300.0],
            subtotal=600.0,
            freight=50.0,
            total=650.0,
        )
        results = validate_commercial_invoice(schema)
        math_checks = [r for r in results if "sum" in r.check_name or "equals" in r.check_name]
        assert all(r.passed for r in math_checks), [r.message for r in math_checks if r.failed]

    def test_wrong_line_total_fails(self):
        from app.validation.validators import validate_commercial_invoice
        schema = self._make_invoice(
            line_totals=[100.0, 200.0, 999.0],  # wrong last item
            subtotal=600.0,
            freight=50.0,
            total=650.0,
        )
        results = validate_commercial_invoice(schema)
        sum_check = next((r for r in results if "sum_to_subtotal" in r.check_name), None)
        assert sum_check is not None
        assert sum_check.failed

    def test_wrong_total_fails(self):
        from app.validation.validators import validate_commercial_invoice
        schema = self._make_invoice(
            line_totals=[100.0, 200.0, 300.0],
            subtotal=600.0,
            freight=50.0,
            total=999.0,  # wrong total
        )
        results = validate_commercial_invoice(schema)
        total_check = next((r for r in results if "equals_total" in r.check_name), None)
        assert total_check is not None
        assert total_check.failed


# ── Packing list weight validator ─────────────────────────────────────────────

class TestPackingListMath:
    def test_weights_match(self):
        from app.schemas.extraction import (
            ExtractedField, Evidence, PackageItem, PackingListSchema, PartyInfo
        )
        from app.validation.validators import validate_packing_list

        def ef(v):
            return ExtractedField(value=str(v), confidence=0.9,
                                   evidence=Evidence(page=1, snippet=str(v)))

        schema = PackingListSchema(
            packing_list_number=ef("PL-001"),
            date=ef("2024-01-15"),
            shipper=PartyInfo(name=ef("Shipper Co")),
            consignee=PartyInfo(name=ef("Consignee Co")),
            packages=[
                PackageItem(gross_weight=ef("100.0")),
                PackageItem(gross_weight=ef("200.0")),
                PackageItem(gross_weight=ef("300.0")),
            ],
            total_packages=ef("3"),
            total_gross_weight=ef("600.0"),
        )
        results = validate_packing_list(schema)
        weight_check = next((r for r in results if "sum_to_total" in r.check_name), None)
        assert weight_check is not None
        assert weight_check.passed

    def test_weight_mismatch_fails(self):
        from app.schemas.extraction import (
            ExtractedField, Evidence, PackageItem, PackingListSchema, PartyInfo
        )
        from app.validation.validators import validate_packing_list

        def ef(v):
            return ExtractedField(value=str(v), confidence=0.9,
                                   evidence=Evidence(page=1, snippet=str(v)))

        schema = PackingListSchema(
            packing_list_number=ef("PL-001"),
            date=ef("2024-01-15"),
            shipper=PartyInfo(name=ef("Shipper Co")),
            consignee=PartyInfo(name=ef("Consignee Co")),
            packages=[
                PackageItem(gross_weight=ef("100.0")),
                PackageItem(gross_weight=ef("200.0")),
            ],
            total_packages=ef("2"),
            total_gross_weight=ef("999.0"),  # wrong
        )
        results = validate_packing_list(schema)
        weight_check = next((r for r in results if "sum_to_total" in r.check_name), None)
        assert weight_check is not None
        assert weight_check.failed
