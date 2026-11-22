"""
Format validators — deterministic checks on field value formats.

These run after extraction and don't require an LLM.
Each function takes a string value and returns True/False.

ISO 6346 container number:
  Format: AAAU 000000 C
    - 3-letter owner code (alpha)
    - 1-letter equipment category (U/J/Z/R)
    - 6-digit serial number
    - 1 check digit (computed from the above 10 characters)

  Check digit algorithm (spec section 4.2):
    1. Assign numeric values: A=10, B=12, C=13, ... Z=38 (skip 11,22,33)
    2. Multiply each position value by 2^position (0-indexed)
    3. Sum all products
    4. Divide sum by 11, take remainder
    5. If remainder == 10, check digit is 0
"""

import re
from datetime import datetime
from typing import Optional


# ── ISO 4217 currency codes (subset of common ones) ──────────────────────────
VALID_CURRENCY_CODES = {
    "USD", "EUR", "GBP", "JPY", "CNY", "HKD", "SGD", "AUD", "CAD",
    "CHF", "KRW", "INR", "BRL", "MXN", "THB", "MYR", "IDR", "VND",
    "NZD", "SEK", "NOK", "DKK", "AED", "SAR",
}

# ── HS code: minimum 6 digits, may contain dots ───────────────────────────────
HS_CODE_RE = re.compile(r"^\d{4}[\.\d]{2,}")

# ── Container number: 4 alpha + 6 digits + 1 check digit ─────────────────────
CONTAINER_RE = re.compile(r"^[A-Z]{4}\d{7}$")

# ISO 6346 char-to-value mapping (letters only; digits are face value)
_CHAR_VALUES: dict[str, int] = {}
_val = 10
for _ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    # Skip multiples of 11 (11, 22, 33)
    while _val % 11 == 0:
        _val += 1
    _CHAR_VALUES[_ch] = _val
    _val += 1


def validate_container_number(container: str) -> bool:
    """
    Validate an ISO 6346 container number including the check digit.
    Input: 'MSCU1234560' (11 chars, no spaces)
    """
    cn = container.upper().replace(" ", "")
    if not CONTAINER_RE.match(cn):
        return False

    # Compute check digit
    total = 0
    for i, ch in enumerate(cn[:10]):
        if ch.isalpha():
            val = _CHAR_VALUES.get(ch, 0)
        else:
            val = int(ch)
        total += val * (2 ** i)

    remainder = total % 11
    check_digit = 0 if remainder == 10 else remainder
    return check_digit == int(cn[10])


def validate_date(date_str: str) -> bool:
    """
    Accept multiple date formats commonly found on shipping documents.
    Returns True if the string is a recognisable date.
    """
    formats = [
        "%Y-%m-%d",      # ISO: 2024-03-15
        "%d/%m/%Y",      # European: 15/03/2024
        "%m/%d/%Y",      # US: 03/15/2024
        "%d-%m-%Y",      # 15-03-2024
        "%d %b %Y",      # 15 Mar 2024
        "%d %B %Y",      # 15 March 2024
        "%B %d, %Y",     # March 15, 2024
        "%d.%m.%Y",      # 15.03.2024
        "%Y%m%d",        # 20240315
    ]
    for fmt in formats:
        try:
            datetime.strptime(date_str.strip(), fmt)
            return True
        except ValueError:
            continue
    return False


def validate_currency_code(code: str) -> bool:
    """Check if a currency code is a known ISO 4217 code."""
    return code.strip().upper() in VALID_CURRENCY_CODES


def validate_hs_code(hs: str) -> bool:
    """
    HS codes are at least 6 significant digits (may include dots).
    Examples: "8471.30", "61091000", "84.71.30"
    """
    digits = re.sub(r"[.\s-]", "", hs)
    return digits.isdigit() and len(digits) >= 6


def validate_numeric(value: str) -> Optional[float]:
    """
    Try to parse a string as a positive number.
    Handles commas as thousands separators: "1,234.56" → 1234.56
    Returns the float or None if unparseable or None input.
    """
    if value is None:
        return None
    cleaned = value.replace(",", "").strip()
    try:
        return float(cleaned)
    except (ValueError, AttributeError):
        return None
