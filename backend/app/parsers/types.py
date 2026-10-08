"""
Shared data types for document parsers.

Both parsers (PDF and OCR) produce the same normalised output structure so the
rest of the pipeline doesn't need to know which parser was used.

Word-level bounding boxes are the foundation for evidence grounding:
  - The LLM returns a text snippet per field.
  - The grounding node finds that snippet in the word list and computes its bbox.
  - The UI renders the bbox as a highlight overlay on the PDF viewer.

Coordinate system: top-left origin, points (1pt = 1/72 inch).
This matches PyMuPDF's native coordinate system. Tesseract returns pixels,
which we normalise to points using DPI (default 300 DPI → 1pt = 300/72 pixels).
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WordBox:
    """A single word with its bounding box."""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int  # 1-indexed


@dataclass
class ParsedPage:
    """All text and word boxes for one page."""
    page_number: int           # 1-indexed
    width: float               # page width in points
    height: float              # page height in points
    raw_text: str              # full page text, newline-separated
    words: list[WordBox] = field(default_factory=list)


@dataclass
class ParsedDocument:
    """
    Complete parse result for one document.
    This is what gets stored in document_pages and consumed by the extract node.
    """
    pages: list[ParsedPage] = field(default_factory=list)
    parser_used: str = ""      # "pymupdf" or "tesseract"
    total_words: int = 0

    @property
    def full_text(self) -> str:
        """All pages joined — used for classification and as LLM context."""
        return "\n\n--- PAGE BREAK ---\n\n".join(p.raw_text for p in self.pages)

    def words_for_page(self, page_number: int) -> list[WordBox]:
        """Get all words for a specific page (1-indexed)."""
        for page in self.pages:
            if page.page_number == page_number:
                return page.words
        return []

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict for storage in document_pages.words."""
        return {
            "parser_used": self.parser_used,
            "pages": [
                {
                    "page_number": p.page_number,
                    "width": p.width,
                    "height": p.height,
                    "raw_text": p.raw_text,
                    "words": [
                        {
                            "text": w.text,
                            "x0": w.x0, "y0": w.y0,
                            "x1": w.x1, "y1": w.y1,
                            "page": w.page,
                        }
                        for w in p.words
                    ],
                }
                for p in self.pages
            ],
        }
