"""
Parser dispatcher — chooses the right parser for each file type.

Decision logic:
  - PDF: try PyMuPDF first; if it looks like a scan (< 20 words/page), fall back to OCR
  - Image (PNG, JPG, TIFF): always use Tesseract OCR

This keeps the pipeline node simple: it calls parse_document() and gets a
ParsedDocument regardless of the underlying parser used.
"""

from pathlib import Path

from app.core.logging import get_logger
from app.parsers.ocr_parser import parse_with_ocr
from app.parsers.pdf_parser import is_scanned_pdf, parse_pdf
from app.parsers.types import ParsedDocument

logger = get_logger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif"}


def parse_document(file_path: str | Path) -> ParsedDocument:
    """
    Parse any supported file type and return a normalised ParsedDocument.

    Raises FileNotFoundError or ValueError on bad input.
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        # Check if the PDF has a real text layer or is just embedded images
        if is_scanned_pdf(path):
            logger.info("using_ocr_for_scanned_pdf", path=str(path))
            return parse_with_ocr(path)
        else:
            logger.info("using_pymupdf_for_digital_pdf", path=str(path))
            return parse_pdf(path)

    elif suffix in IMAGE_EXTENSIONS:
        logger.info("using_ocr_for_image", path=str(path))
        return parse_with_ocr(path)

    else:
        raise ValueError(
            f"Unsupported file type: {suffix}. "
            f"Supported: .pdf, {', '.join(sorted(IMAGE_EXTENSIONS))}"
        )
