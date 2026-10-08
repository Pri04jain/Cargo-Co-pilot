"""
Evidence grounding: match LLM text snippets to word bounding boxes.

The LLM returns a `snippet` per extracted field — the exact text it found in
the document. Our job is to locate that snippet in the parsed word list and
compute a bounding box for the highlight overlay.

Strategy:
  1. Normalize both snippet and word text (lowercase, collapse whitespace)
  2. Use a sliding window over the word list to find the best match
  3. If exact match fails, try a token-overlap fuzzy match
  4. Return the union bbox of all matched words

Why not just use string search on raw_text?
  Raw text is reconstructed from words; line breaks and spacing may differ from
  the original. Working at the word level lets us compute precise bboxes even
  when the snippet spans a line break.

Confidence adjustment rules:
  - Full exact match → no confidence penalty
  - Fuzzy match (token overlap >= 80%) → -0.1 confidence penalty
  - No match found → status=ungrounded, confidence halved
"""

import re
from typing import Optional

from app.parsers.types import WordBox
from app.schemas.extraction import BBox, Evidence, ExtractedField, FieldStatus

# Minimum fraction of snippet tokens that must match a window (fuzzy fallback)
FUZZY_MATCH_THRESHOLD = 0.8

# Confidence penalty when we use fuzzy matching instead of exact match
FUZZY_CONFIDENCE_PENALTY = 0.1


def ground_field(
    field: ExtractedField,
    words_by_page: dict[int, list[WordBox]],
) -> ExtractedField:
    """
    Attempt to ground a single extracted field.

    Modifies and returns a copy of the field with:
      - evidence.bbox filled in (if found)
      - status set to grounded/ungrounded
      - confidence adjusted

    Args:
        field: The extracted field with evidence.snippet set by the LLM.
        words_by_page: Dict mapping page_number → list of WordBox objects.
    """
    if field.value is None or field.evidence is None or not field.evidence.snippet:
        # Nothing to ground
        return field.model_copy(update={"status": FieldStatus.UNGROUNDED})

    snippet = field.evidence.snippet
    page_num = field.evidence.page

    words = words_by_page.get(page_num, [])
    if not words:
        # Try all pages if page number seems wrong
        for pg_words in words_by_page.values():
            result = _find_snippet(snippet, pg_words)
            if result is not None:
                bbox, exact = result
                words = pg_words  # found it
                break
        else:
            return field.model_copy(update={
                "status": FieldStatus.UNGROUNDED,
                "confidence": field.confidence * 0.5,
            })
    else:
        result = _find_snippet(snippet, words)

    if result is None:
        # Not found on the specified page — try other pages
        for pg_num, pg_words in words_by_page.items():
            if pg_num == page_num:
                continue
            result = _find_snippet(snippet, pg_words)
            if result is not None:
                page_num = pg_num
                break

    if result is None:
        return field.model_copy(update={
            "status": FieldStatus.UNGROUNDED,
            "confidence": field.confidence * 0.5,
        })

    bbox, exact_match = result
    confidence = field.confidence
    if not exact_match:
        confidence = max(0.0, confidence - FUZZY_CONFIDENCE_PENALTY)

    updated_evidence = field.evidence.model_copy(update={
        "bbox": bbox,
        "page": page_num,
    })
    return field.model_copy(update={
        "evidence": updated_evidence,
        "status": FieldStatus.GROUNDED,
        "confidence": round(confidence, 3),
    })


def _find_snippet(
    snippet: str,
    words: list[WordBox],
) -> Optional[tuple[BBox, bool]]:
    """
    Search for a snippet in a list of word boxes.

    Returns (BBox, is_exact_match) or None if not found.
    The BBox is the union of all matched word boxes.
    """
    if not snippet or not words:
        return None

    snippet_tokens = _tokenize(snippet)
    if not snippet_tokens:
        return None

    n = len(snippet_tokens)

    # ── Pass 1: exact token match ─────────────────────────────────────────────
    word_tokens = [_normalize(w.text) for w in words]

    for i in range(len(word_tokens) - n + 1):
        window = word_tokens[i:i + n]
        if window == snippet_tokens:
            matched_words = words[i:i + n]
            return _union_bbox(matched_words), True

    # ── Pass 2: fuzzy match — allow partial overlap ───────────────────────────
    # Useful when OCR introduces minor spacing/hyphenation differences
    best_overlap = 0.0
    best_start = -1

    snippet_set = set(snippet_tokens)
    for i in range(len(word_tokens) - n + 1):
        window_set = set(word_tokens[i:i + n])
        overlap = len(snippet_set & window_set) / len(snippet_set)
        if overlap > best_overlap:
            best_overlap = overlap
            best_start = i

    if best_overlap >= FUZZY_MATCH_THRESHOLD and best_start >= 0:
        matched_words = words[best_start:best_start + n]
        return _union_bbox(matched_words), False

    return None


def _tokenize(text: str) -> list[str]:
    """Normalize and split text into tokens for comparison."""
    return [_normalize(t) for t in text.split() if t.strip()]


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation from edges, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"^[^\w]+|[^\w]+$", "", text)  # strip leading/trailing non-word chars
    return text


def _union_bbox(words: list[WordBox]) -> BBox:
    """Compute the bounding box that contains all given words."""
    return BBox(
        x0=min(w.x0 for w in words),
        y0=min(w.y0 for w in words),
        x1=max(w.x1 for w in words),
        y1=max(w.y1 for w in words),
    )
