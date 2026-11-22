"""
PDF parser using PyMuPDF (fitz).

Why PyMuPDF over pdfplumber?
- Faster (C extension, not pure Python)
- Richer word-box API: get_text("words") returns (x0,y0,x1,y1,word,block,line,word_idx)
- Handles complex layouts (multi-column, rotated text) better

For each page we extract:
  1. Words with bounding boxes — used for evidence grounding
  2. Raw text (sorted by reading order) — used as LLM input

We do NOT send bounding boxes to the LLM. The LLM just sees text.
Bounding boxes are used after extraction to locate where the LLM's cited snippet
appears on the page.
"""

from pathlib import Path

import fitz  # PyMuPDF

from app.core.logging import get_logger
from app.parsers.types import ParsedDocument, ParsedPage, WordBox

logger = get_logger(__name__)


def parse_pdf(file_path: str | Path) -> ParsedDocument:
    """
    Parse a digital PDF and return words with bounding boxes for every page.

    Raises FileNotFoundError if the file doesn't exist.
    Raises ValueError if the file is not a valid PDF.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {file_path}")

    try:
        doc = fitz.open(str(path))
    except Exception as e:
        raise ValueError(f"Cannot open as PDF: {e}") from e

    parsed = ParsedDocument(parser_used="pymupdf")

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        page_number = page_idx + 1  # 1-indexed

        # get_text("words") returns a list of tuples:
        # (x0, y0, x1, y1, word_text, block_no, line_no, word_no)
        raw_words = page.get_text("words", sort=True)  # sort=True → reading order

        words: list[WordBox] = []
        for (x0, y0, x1, y1, text, *_) in raw_words:
            # Skip whitespace-only "words" (PyMuPDF sometimes returns these)
            if text.strip():
                words.append(WordBox(
                    text=text.strip(),
                    x0=round(x0, 2),
                    y0=round(y0, 2),
                    x1=round(x1, 2),
                    y1=round(y1, 2),
                    page=page_number,
                ))

        # Build raw text from sorted words (preserves reading order better
        # than page.get_text("text") for multi-column layouts)
        raw_text = _words_to_text(words, page.rect.height)

        rect = page.rect
        parsed_page = ParsedPage(
            page_number=page_number,
            width=round(rect.width, 2),
            height=round(rect.height, 2),
            raw_text=raw_text,
            words=words,
        )
        parsed.pages.append(parsed_page)

    doc.close()
    parsed.total_words = sum(len(p.words) for p in parsed.pages)

    logger.debug(
        "pdf_parsed",
        path=str(path),
        pages=len(parsed.pages),
        words=parsed.total_words,
    )
    return parsed


def _words_to_text(words: list[WordBox], page_height: float) -> str:
    """
    Convert a list of word boxes to a readable string.

    Strategy: group words into lines by Y position (within a tolerance),
    then join lines with newlines and words within a line with spaces.
    This preserves table structure better than just sorting by x,y.
    """
    if not words:
        return ""

    LINE_TOLERANCE = 5.0  # points — words within this Y range are on the same line

    # Group by approximate Y position
    lines: dict[int, list[WordBox]] = {}
    for word in words:
        # Round y0 to nearest line bucket
        line_key = int(word.y0 / LINE_TOLERANCE)
        lines.setdefault(line_key, []).append(word)

    # Sort line buckets top-to-bottom, words left-to-right within each line
    sorted_lines = sorted(lines.items(), key=lambda kv: kv[0])
    text_lines = []
    for _, line_words in sorted_lines:
        line_words.sort(key=lambda w: w.x0)
        text_lines.append(" ".join(w.text for w in line_words))

    return "\n".join(text_lines)


def is_scanned_pdf(file_path: str | Path) -> bool:
    """
    Heuristic: if a PDF has very few words per page, it's probably a scan
    (images embedded in a PDF wrapper with no text layer).
    We fall back to OCR in this case.
    """
    try:
        doc = fitz.open(str(file_path))
        total_words = 0
        for page in doc:
            total_words += len(page.get_text("words"))
        doc.close()
        avg_words_per_page = total_words / max(len(doc), 1)
        # Fewer than 20 words per page → probably a scan
        return avg_words_per_page < 20
    except Exception:
        return False
