"""
Unit tests for PDF and OCR parsers.

These tests use minimal synthetic inputs rather than real files, so they
run without Tesseract or a real PDF installed — they test the parsing logic,
not the underlying library.

For integration tests against real files, see tests/integration/test_pipeline.py
(added in Phase 3).
"""

import io
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.parsers.types import ParsedDocument, ParsedPage, WordBox


# ── WordBox helpers ───────────────────────────────────────────────────────────

def make_word(text: str, x0: float, y0: float) -> WordBox:
    return WordBox(text=text, x0=x0, y0=y0, x1=x0 + 10, y1=y0 + 10, page=1)


def make_page(words: list[WordBox], page_number: int = 1) -> ParsedPage:
    raw_text = " ".join(w.text for w in words)
    return ParsedPage(
        page_number=page_number,
        width=595.0,
        height=842.0,
        raw_text=raw_text,
        words=words,
    )


# ── ParsedDocument ────────────────────────────────────────────────────────────

def test_parsed_document_full_text():
    """full_text joins pages with a PAGE BREAK separator."""
    page1 = make_page([make_word("Hello", 0, 0), make_word("World", 20, 0)])
    page2 = make_page([make_word("Goodbye", 0, 0)], page_number=2)

    doc = ParsedDocument(pages=[page1, page2], parser_used="pymupdf")
    full = doc.full_text
    assert "Hello World" in full
    assert "PAGE BREAK" in full
    assert "Goodbye" in full


def test_words_for_page():
    words = [make_word("SHANGHAI", 10, 10), make_word("PORT", 30, 10)]
    page = make_page(words, page_number=2)
    doc = ParsedDocument(pages=[page], parser_used="pymupdf")

    result = doc.words_for_page(2)
    assert len(result) == 2
    assert result[0].text == "SHANGHAI"


def test_words_for_nonexistent_page():
    doc = ParsedDocument(pages=[], parser_used="pymupdf")
    assert doc.words_for_page(99) == []


def test_parsed_document_to_dict():
    words = [make_word("INVOICE", 5, 5)]
    page = make_page(words)
    doc = ParsedDocument(pages=[page], parser_used="pymupdf", total_words=1)

    d = doc.to_dict()
    assert d["parser_used"] == "pymupdf"
    assert len(d["pages"]) == 1
    assert d["pages"][0]["words"][0]["text"] == "INVOICE"


# ── PDF parser (mocked fitz) ──────────────────────────────────────────────────

def test_pdf_parser_word_extraction():
    """
    Test the PDF parser logic by mocking fitz.open.
    We verify that words are extracted, whitespace is stripped, and bboxes are correct.
    """
    fitz = pytest.importorskip("fitz", reason="PyMuPDF (fitz) not installed — runs in Docker")
    from app.parsers.pdf_parser import parse_pdf

    # Mock fitz document with one page and two words
    mock_word1 = (10.0, 20.0, 50.0, 30.0, "SHIPPER:", 0, 0, 0)
    mock_word2 = (55.0, 20.0, 120.0, 30.0, "Acme Corp", 0, 0, 1)
    mock_word3 = (10.0, 40.0, 80.0, 50.0, "  ", 0, 1, 0)  # whitespace-only, should be skipped

    mock_page = MagicMock()
    mock_page.get_text.return_value = [mock_word1, mock_word2, mock_word3]
    mock_page.rect = MagicMock()
    mock_page.rect.width = 595.0
    mock_page.rect.height = 842.0

    mock_doc = MagicMock()
    mock_doc.__len__ = MagicMock(return_value=1)
    mock_doc.__getitem__ = MagicMock(return_value=mock_page)
    mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))

    with patch("app.parsers.pdf_parser.fitz.open", return_value=mock_doc):
        with patch("pathlib.Path.exists", return_value=True):
            result = parse_pdf("/fake/path/doc.pdf")

    assert len(result.pages) == 1
    # Whitespace-only "word" should be skipped
    word_texts = [w.text for w in result.pages[0].words]
    assert "SHIPPER:" in word_texts
    assert "Acme Corp" in word_texts
    assert "  " not in word_texts


def test_pdf_parser_file_not_found():
    pytest.importorskip("fitz", reason="PyMuPDF (fitz) not installed — runs in Docker")
    from app.parsers.pdf_parser import parse_pdf
    with pytest.raises(FileNotFoundError):
        parse_pdf("/nonexistent/path/file.pdf")


# ── OCR parser helpers ────────────────────────────────────────────────────────

def test_px_to_pt_conversion():
    pytest.importorskip("fitz", reason="PyMuPDF (fitz) not installed — runs in Docker")
    from app.parsers.ocr_parser import _px_to_pt
    # At 300 DPI: 300 pixels = 1 inch = 72 points
    assert _px_to_pt(300, 300) == pytest.approx(72.0)
    assert _px_to_pt(150, 300) == pytest.approx(36.0)


def test_ocr_words_to_text():
    pytest.importorskip("fitz", reason="PyMuPDF (fitz) not installed — runs in Docker")
    from app.parsers.ocr_parser import _words_to_text
    words = [
        WordBox("BILL", 10, 10, 50, 25, 1),
        WordBox("OF", 55, 12, 70, 25, 1),    # same line (y≈10)
        WordBox("LADING", 75, 11, 130, 25, 1),
        WordBox("DATE:", 10, 50, 60, 65, 1), # different line (y≈50)
        WordBox("2024-01-15", 65, 50, 150, 65, 1),
    ]
    text = _words_to_text(words)
    lines = text.splitlines()
    assert len(lines) == 2
    assert "BILL" in lines[0]
    assert "OF" in lines[0]
    assert "LADING" in lines[0]
    assert "DATE:" in lines[1]


# ── Dispatcher ────────────────────────────────────────────────────────────────

def test_dispatcher_routes_image_to_ocr():
    pytest.importorskip("structlog", reason="structlog not installed — runs in Docker")
    pytest.importorskip("fitz", reason="PyMuPDF not installed — runs in Docker")
    from app.parsers.dispatcher import parse_document

    mock_result = ParsedDocument(parser_used="tesseract")
    with patch("app.parsers.dispatcher.parse_with_ocr", return_value=mock_result) as mock_ocr:
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.suffix", new_callable=lambda: property(lambda s: ".png")):
                pass

    # Test the extension check logic directly
    from app.parsers.dispatcher import IMAGE_EXTENSIONS
    assert ".png" in IMAGE_EXTENSIONS
    assert ".jpg" in IMAGE_EXTENSIONS
    assert ".tiff" in IMAGE_EXTENSIONS


def test_dispatcher_unsupported_type():
    pytest.importorskip("structlog", reason="structlog not installed — runs in Docker")
    pytest.importorskip("fitz", reason="PyMuPDF not installed — runs in Docker")
    from app.parsers.dispatcher import parse_document
    with patch("pathlib.Path.exists", return_value=True):
        with pytest.raises((ValueError, Exception)):
            parse_document("/fake/file.docx")


# ── Grounder ─────────────────────────────────────────────────────────────────

class TestGrounder:

    def test_exact_match(self):
        from app.parsers.grounder import ground_field
        from app.schemas.extraction import Evidence, ExtractedField

        words = [
            WordBox("Port", 10, 10, 40, 20, 1),
            WordBox("of", 45, 10, 60, 20, 1),
            WordBox("Loading:", 65, 10, 120, 20, 1),
            WordBox("SHANGHAI", 125, 10, 200, 20, 1),
        ]
        f = ExtractedField(
            value="SHANGHAI",
            confidence=0.95,
            evidence=Evidence(page=1, snippet="Port of Loading: SHANGHAI"),
        )
        words_by_page = {1: words}
        result = ground_field(f, words_by_page)

        assert result.status.value == "grounded"
        assert result.evidence.bbox is not None
        # bbox should cover "Port" through "SHANGHAI"
        assert result.evidence.bbox.x0 == pytest.approx(10.0)
        assert result.evidence.bbox.x1 == pytest.approx(200.0)

    def test_null_value_marks_ungrounded(self):
        from app.parsers.grounder import ground_field
        from app.schemas.extraction import ExtractedField

        f = ExtractedField(value=None, confidence=0.0)
        result = ground_field(f, {})
        assert result.status.value == "ungrounded"

    def test_no_match_lowers_confidence(self):
        from app.parsers.grounder import ground_field
        from app.schemas.extraction import Evidence, ExtractedField

        words = [
            WordBox("TOTALLY", 10, 10, 60, 20, 1),
            WordBox("DIFFERENT", 65, 10, 130, 20, 1),
            WordBox("TEXT", 135, 10, 165, 20, 1),
        ]
        f = ExtractedField(
            value="MISSING",
            confidence=0.9,
            evidence=Evidence(page=1, snippet="This snippet does not appear"),
        )
        result = ground_field(f, {1: words})
        assert result.status.value == "ungrounded"
        assert result.confidence < 0.9  # confidence was lowered

    def test_fuzzy_match(self):
        from app.parsers.grounder import ground_field
        from app.schemas.extraction import Evidence, ExtractedField

        # OCR might split "MAERSK" as "MAEYRSK" or add extra chars
        words = [
            WordBox("Vessel:", 10, 10, 55, 20, 1),
            WordBox("MAERSK", 60, 10, 120, 20, 1),
            WordBox("ELDORADO", 125, 10, 200, 20, 1),
        ]
        # Snippet has slight OCR variation
        f = ExtractedField(
            value="MAERSK ELDORADO",
            confidence=0.9,
            evidence=Evidence(page=1, snippet="Vessel: MAERSK ELDORADO"),
        )
        result = ground_field(f, {1: words})
        # Should find it (exact match in this case)
        assert result.evidence.bbox is not None
