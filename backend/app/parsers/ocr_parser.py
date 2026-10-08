"""
OCR parser using Tesseract via pytesseract.

Used for:
  - Scanned image files (PNG, JPG, TIFF)
  - PDFs that have been identified as scans (no text layer)

Pipeline:
  1. Detect and correct rotation (Tesseract OSD mode)
  2. Pre-process image (greyscale, mild denoising)
  3. Run Tesseract with `image_to_data` to get word-level bounding boxes
  4. Filter low-confidence OCR tokens (< 30% confidence are likely artefacts)
  5. Return same ParsedDocument structure as the PDF parser

Coordinate system: Tesseract returns pixel coordinates.
We convert to points using the image DPI (default 300 DPI):
  points = pixels * 72 / DPI

Why 300 DPI as default?
  Standard scanning resolution for documents. If the source DPI is embedded
  in the image metadata we use that instead.
"""

import io
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF — used to render PDF pages to images
import pytesseract
from PIL import Image, ImageFilter, ImageOps

from app.core.logging import get_logger
from app.parsers.types import ParsedDocument, ParsedPage, WordBox

logger = get_logger(__name__)

DEFAULT_DPI = 300
MIN_CONFIDENCE = 30  # Tesseract confidence threshold (0–100)
RENDER_DPI = 300     # DPI used when rendering PDF pages to images for OCR


def parse_with_ocr(file_path: str | Path) -> ParsedDocument:
    """
    Parse a scanned image or image-based PDF using Tesseract OCR.

    Handles: PNG, JPG, TIFF (direct), and PDF (rendered page-by-page).
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    suffix = path.suffix.lower()

    if suffix == ".pdf":
        pages = _parse_pdf_with_ocr(path)
    elif suffix in (".png", ".jpg", ".jpeg", ".tiff", ".tif"):
        pages = _parse_image_with_ocr(path)
    else:
        raise ValueError(f"Unsupported file type for OCR: {suffix}")

    doc = ParsedDocument(parser_used="tesseract", pages=pages)
    doc.total_words = sum(len(p.words) for p in pages)

    logger.debug(
        "ocr_parsed",
        path=str(path),
        pages=len(pages),
        words=doc.total_words,
    )
    return doc


def _parse_pdf_with_ocr(path: Path) -> list[ParsedPage]:
    """Render each PDF page to an image, then OCR it."""
    pages = []
    doc = fitz.open(str(path))

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        # Render at RENDER_DPI for good OCR quality
        mat = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_bytes))

        parsed_page = _ocr_image(img, page_number=page_idx + 1, dpi=RENDER_DPI)
        # Use actual PDF page dimensions (points) for bbox scaling
        parsed_page.width = round(page.rect.width, 2)
        parsed_page.height = round(page.rect.height, 2)
        pages.append(parsed_page)

    doc.close()
    return pages


def _parse_image_with_ocr(path: Path) -> list[ParsedPage]:
    """OCR a single image file (treated as page 1)."""
    img = Image.open(str(path))
    dpi = _get_image_dpi(img)
    parsed_page = _ocr_image(img, page_number=1, dpi=dpi)
    return [parsed_page]


def _ocr_image(img: Image.Image, page_number: int, dpi: int = DEFAULT_DPI) -> ParsedPage:
    """
    Core OCR logic for one image.

    Steps:
    1. Convert to greyscale (improves OCR accuracy on colour documents)
    2. Detect and correct rotation
    3. Run Tesseract with word-level output
    4. Filter low-confidence tokens and convert pixel→point coordinates
    """
    # Step 1: Greyscale
    grey = ImageOps.grayscale(img)

    # Step 2: Deskew — detect rotation angle and correct it
    grey = _deskew(grey)

    # Step 3: Mild denoising (helps with scanned documents)
    grey = grey.filter(ImageFilter.MedianFilter(size=1))  # size=1 is a no-op on clean images

    # Step 4: Run Tesseract
    # image_to_data returns a TSV string with confidence, bboxes, and text per word
    tsv_data = pytesseract.image_to_data(
        grey,
        output_type=pytesseract.Output.DICT,
        config="--psm 3",  # PSM 3 = fully automatic page segmentation (default)
    )

    words: list[WordBox] = []
    n_boxes = len(tsv_data["text"])

    for i in range(n_boxes):
        conf = int(tsv_data["conf"][i])
        text = tsv_data["text"][i].strip()

        # Skip blank tokens and low-confidence results
        if not text or conf < MIN_CONFIDENCE:
            continue

        # Convert pixel coordinates to points
        px_x = tsv_data["left"][i]
        px_y = tsv_data["top"][i]
        px_w = tsv_data["width"][i]
        px_h = tsv_data["height"][i]

        x0 = round(_px_to_pt(px_x, dpi), 2)
        y0 = round(_px_to_pt(px_y, dpi), 2)
        x1 = round(_px_to_pt(px_x + px_w, dpi), 2)
        y1 = round(_px_to_pt(px_y + px_h, dpi), 2)

        words.append(WordBox(
            text=text,
            x0=x0, y0=y0, x1=x1, y1=y1,
            page=page_number,
        ))

    raw_text = _words_to_text(words)

    # Page dimensions in points (image size ÷ DPI × 72)
    width_pt = round(_px_to_pt(img.width, dpi), 2)
    height_pt = round(_px_to_pt(img.height, dpi), 2)

    return ParsedPage(
        page_number=page_number,
        width=width_pt,
        height=height_pt,
        raw_text=raw_text,
        words=words,
    )


def _deskew(img: Image.Image) -> Image.Image:
    """
    Detect document rotation using Tesseract's OSD (Orientation and Script Detection)
    and rotate to correct it.

    If OSD fails (e.g. not enough text for detection), return the original image.
    This is a common failure on very sparse documents — we log a warning but don't crash.
    """
    try:
        osd = pytesseract.image_to_osd(img, config="--psm 0 -c min_characters_to_try=5")
        # Parse the rotation angle from OSD output
        for line in osd.splitlines():
            if "Rotate:" in line:
                angle = int(line.split(":")[1].strip())
                if angle != 0:
                    logger.debug("deskewing_image", angle=angle)
                    # Negative angle because PIL rotates counter-clockwise
                    return img.rotate(-angle, expand=True, fillcolor=255)
    except pytesseract.TesseractError:
        # Not enough content for OSD — fine, proceed without deskew
        pass
    except Exception as e:
        logger.warning("deskew_failed", error=str(e))

    return img


def _px_to_pt(pixels: int, dpi: int) -> float:
    """Convert pixel coordinates to PDF points (72 points per inch)."""
    return pixels * 72.0 / dpi


def _get_image_dpi(img: Image.Image) -> int:
    """Try to read DPI from image metadata; fall back to DEFAULT_DPI."""
    try:
        dpi_info = img.info.get("dpi")
        if dpi_info and isinstance(dpi_info, (tuple, list)):
            return int(dpi_info[0])
    except Exception:
        pass
    return DEFAULT_DPI


def _words_to_text(words: list[WordBox]) -> str:
    """Convert word boxes to readable text, preserving approximate line structure."""
    if not words:
        return ""

    LINE_TOLERANCE = 8.0  # slightly higher than PDF parser due to OCR noise

    lines: dict[int, list[WordBox]] = {}
    for word in words:
        line_key = int(word.y0 / LINE_TOLERANCE)
        lines.setdefault(line_key, []).append(word)

    sorted_lines = sorted(lines.items(), key=lambda kv: kv[0])
    text_lines = []
    for _, line_words in sorted_lines:
        line_words.sort(key=lambda w: w.x0)
        text_lines.append(" ".join(w.text for w in line_words))

    return "\n".join(text_lines)
