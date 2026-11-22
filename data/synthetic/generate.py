#!/usr/bin/env python3
"""
Synthetic shipping document generator.

Produces 40+ documents across 3 types with ground-truth JSON labels.
All documents use purely fictional company names, addresses, and numbers.

Output structure:
  data/synthetic/train/   — 30 documents (used for prompt development)
  data/synthetic/test/    — 10 documents (held out, never used for tuning)
  data/synthetic/pos/     — matching Purchase Orders (JSON)

Document conditions generated:
  - Clean digital PDFs (standard layout)
  - Rotated/noisy scanned images (PIL image manipulation)
  - Different layouts (header styles, table formats)
  - Missing required fields (exercises validator)
  - Deliberate mismatches (wrong total, wrong weight, wrong consignee)

Each document gets a <filename>.json ground-truth label with:
  - doc_type
  - expected_fields: {field_name: expected_value}
  - expected_routing: auto_approve | manager_approval | exception_queue
  - issues: list of deliberate problems introduced
  - split: train | test

Usage:
  python data/synthetic/generate.py
  # or via Makefile:
  make generate-data

Requirements: reportlab (PDF generation), Pillow (image manipulation)
Install: pip install reportlab Pillow
"""

import json
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# ── Try to import reportlab; give a clear error if missing ───────────────────
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.pdfgen import canvas as rl_canvas
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

from PIL import Image, ImageDraw, ImageFilter, ImageFont
import io

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
TRAIN_DIR = ROOT / "train"
TEST_DIR = ROOT / "test"
PO_DIR = ROOT / "pos"

TRAIN_DIR.mkdir(exist_ok=True)
TEST_DIR.mkdir(exist_ok=True)
PO_DIR.mkdir(exist_ok=True)

random.seed(42)  # reproducible generation

# ── Fictional data pools ──────────────────────────────────────────────────────
COMPANIES = [
    "Shenzhen Electronics Co. Ltd",
    "Shanghai Textile Exports Ltd",
    "Vietnam Manufacturing Corp",
    "Korea Parts & Components Co.",
    "Indonesia Raw Materials Pte Ltd",
    "Guangzhou Trade International",
    "Bangkok Garment Factory Co.",
    "Mumbai Spice Exports Pvt Ltd",
    "Global Tech Distribution Inc",
    "Pacific Rim Logistics Ltd",
]

ADDRESSES = [
    "12 Industrial Park Rd, Shenzhen 518000, China",
    "88 Huangpu District, Shanghai 200001, China",
    "23 Bien Hoa Industrial Zone, Vietnam",
    "15 Busan Port Zone, Busan 48000, Korea",
    "7 Tanjung Priok Industrial Area, Jakarta, Indonesia",
    "Unit 5, Kwai Chung Container Terminal, Hong Kong",
    "45 Lat Krabang Industrial Estate, Bangkok, Thailand",
    "Plot 12, JNPT Special Economic Zone, Mumbai 400707, India",
    "3500 Commerce Drive, Los Angeles CA 90058, USA",
    "88 Botany Road, Sydney NSW 2019, Australia",
]

VESSELS = [
    ("MSC AURORA", "AS2401W"),
    ("EVER GIVEN II", "EG2402E"),
    ("COSCO SHIPPING GALAXY", "CG2403W"),
    ("MAERSK ELDORADO", "ME2404E"),
    ("CMA CGM VOLTAIRE", "CV2405W"),
    ("HAPAG BERLIN", "HB2406E"),
]

PORTS = [
    "SHANGHAI", "SHENZHEN", "NINGBO", "GUANGZHOU",
    "BUSAN", "SINGAPORE", "PORT KLANG", "LAEM CHABANG",
    "LOS ANGELES", "LONG BEACH", "ROTTERDAM", "HAMBURG",
    "SYDNEY", "MELBOURNE",
]

HS_CODES = [
    "8471.30", "6109.10", "3924.10", "8517.12",
    "9403.20", "6204.62", "3926.90", "8544.42",
    "7326.90", "6302.60",
]

INCOTERMS = ["FOB", "CIF", "EXW", "DAP", "CFR", "DDP"]
CURRENCIES = ["USD", "EUR", "GBP"]
FREIGHT_TERMS = ["PREPAID", "COLLECT", "AS ARRANGED"]


def random_date(start_year: int = 2024) -> date:
    base = date(start_year, 1, 1)
    return base + timedelta(days=random.randint(0, 364))


def random_container() -> str:
    """Generate a plausible container number (not a valid ISO 6346 check digit — synthetic only)."""
    prefix = random.choice(["MSCU", "TEMU", "HLXU", "CMAU", "COSCO"])
    digits = "".join(str(random.randint(0, 9)) for _ in range(6))
    check = random.randint(0, 9)
    return f"{prefix}{digits}{check}"


def random_seal() -> str:
    return f"SL{random.randint(100000, 999999)}"


def random_po_number() -> str:
    return f"PO-{random.randint(1000, 9999)}-{random.choice(['A', 'B', 'C'])}"


# ── Ground truth label ────────────────────────────────────────────────────────

@dataclass
class GroundTruth:
    doc_type: str
    split: str  # "train" or "test"
    expected_fields: dict = field(default_factory=dict)
    expected_routing: str = "needs_review"
    issues: list[str] = field(default_factory=list)
    po_number: Optional[str] = None
    po_total: Optional[float] = None
    po_currency: Optional[str] = None


# ── PDF generation helpers ────────────────────────────────────────────────────

def _make_pdf_bol(output_path: Path, data: dict) -> None:
    """Generate a Bill of Lading PDF using reportlab."""
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
    )
    styles = getSampleStyleSheet()
    elements = []

    # Header
    elements.append(Paragraph("<b>BILL OF LADING</b>", styles["Title"]))
    elements.append(Spacer(1, 6 * mm))

    # BL number and date
    header_data = [
        ["B/L Number:", data["bl_number"], "Date of Issue:", data["date_of_issue"]],
        ["Vessel:", data["vessel"], "Voyage:", data["voyage"]],
    ]
    t = Table(header_data, colWidths=[40*mm, 60*mm, 40*mm, 40*mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
        ("BACKGROUND", (2, 0), (2, -1), colors.lightgrey),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 4 * mm))

    # Parties
    party_data = [
        ["SHIPPER:", data["shipper_name"]],
        ["", data["shipper_address"]],
        ["CONSIGNEE:", data["consignee_name"]],
        ["", data["consignee_address"]],
        ["NOTIFY PARTY:", data.get("notify_party", data["consignee_name"])],
    ]
    t2 = Table(party_data, colWidths=[40*mm, 150*mm])
    t2.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
    ]))
    elements.append(t2)
    elements.append(Spacer(1, 4 * mm))

    # Routing
    routing_data = [
        ["PORT OF LOADING:", data["port_of_loading"], "PORT OF DISCHARGE:", data["port_of_discharge"]],
        ["FREIGHT TERMS:", data["freight_terms"], "INCOTERM:", data.get("incoterm", "FOB")],
    ]
    t3 = Table(routing_data, colWidths=[45*mm, 50*mm, 50*mm, 45*mm])
    t3.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
        ("BACKGROUND", (2, 0), (2, -1), colors.lightgrey),
    ]))
    elements.append(t3)
    elements.append(Spacer(1, 4 * mm))

    # Container / cargo details
    cargo_data = [
        ["CONTAINER NOS.", "SEAL NOS.", "PACKAGES", "GROSS WEIGHT", "DESCRIPTION"],
        [
            "\n".join(data["container_numbers"]),
            "\n".join(data["seal_numbers"]),
            str(data["packages_count"]),
            f"{data['gross_weight']} {data['weight_unit']}",
            data["goods_description"],
        ],
    ]
    t4 = Table(cargo_data, colWidths=[40*mm, 30*mm, 25*mm, 35*mm, 60*mm])
    t4.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elements.append(t4)

    doc.build(elements)


def _make_pdf_invoice(output_path: Path, data: dict) -> None:
    """Generate a Commercial Invoice PDF."""
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
    )
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("<b>COMMERCIAL INVOICE</b>", styles["Title"]))
    elements.append(Spacer(1, 5 * mm))

    # Invoice header
    header_data = [
        ["Invoice No.:", data["invoice_number"], "Date:", data["invoice_date"]],
        ["PO Reference:", data.get("po_reference", "N/A"), "Payment Terms:", data.get("payment_terms", "NET 30")],
        ["Incoterm:", data.get("incoterm", "FOB"), "Currency:", data["currency"]],
    ]
    t = Table(header_data, colWidths=[35*mm, 65*mm, 35*mm, 55*mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
        ("BACKGROUND", (2, 0), (2, -1), colors.lightgrey),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 4 * mm))

    # Seller / buyer
    party_data = [
        ["SELLER:", data["seller_name"], "BUYER:", data["buyer_name"]],
        ["", data["seller_address"], "", data["buyer_address"]],
    ]
    t2 = Table(party_data, colWidths=[20*mm, 75*mm, 20*mm, 75*mm])
    t2.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
        ("BACKGROUND", (2, 0), (2, -1), colors.lightgrey),
    ]))
    elements.append(t2)
    elements.append(Spacer(1, 4 * mm))

    # Line items
    line_data = [["#", "Description", "HS Code", "Qty", "Unit Price", "Line Total"]]
    for i, item in enumerate(data["line_items"], 1):
        line_data.append([
            str(i),
            item["description"],
            item["hs_code"],
            str(item["quantity"]),
            f"{data['currency']} {item['unit_price']:.2f}",
            f"{data['currency']} {item['line_total']:.2f}",
        ])

    t3 = Table(line_data, colWidths=[10*mm, 60*mm, 25*mm, 15*mm, 30*mm, 30*mm])
    t3.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
    ]))
    elements.append(t3)
    elements.append(Spacer(1, 3 * mm))

    # Totals
    totals_data = [
        ["", "", "", "", "Subtotal:", f"{data['currency']} {data['subtotal']:.2f}"],
        ["", "", "", "", "Freight/Tax:", f"{data['currency']} {data.get('tax_or_freight', 0.0):.2f}"],
        ["", "", "", "", "TOTAL:", f"{data['currency']} {data['total_amount']:.2f}"],
    ]
    t4 = Table(totals_data, colWidths=[10*mm, 60*mm, 25*mm, 15*mm, 30*mm, 30*mm])
    t4.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (4, 2), (4, 2), "Helvetica-Bold"),
        ("FONTNAME", (5, 2), (5, 2), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
        ("LINEABOVE", (4, 2), (5, 2), 1, colors.black),
    ]))
    elements.append(t4)

    doc.build(elements)


def _make_pdf_packing(output_path: Path, data: dict) -> None:
    """Generate a Packing List PDF."""
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
    )
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("<b>PACKING LIST</b>", styles["Title"]))
    elements.append(Spacer(1, 5 * mm))

    header_data = [
        ["Packing List No.:", data["pl_number"], "Date:", data["date"]],
        ["Shipper:", data["shipper_name"], "Consignee:", data["consignee_name"]],
    ]
    t = Table(header_data, colWidths=[40*mm, 60*mm, 30*mm, 60*mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
        ("BACKGROUND", (2, 0), (2, -1), colors.lightgrey),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 4 * mm))

    # Packages
    pkg_data = [["Pkg #", "Description", "Qty", "Net Weight", "Gross Weight", "Dimensions"]]
    for pkg in data["packages"]:
        pkg_data.append([
            pkg["package_id"],
            pkg["description"],
            str(pkg["quantity"]),
            f"{pkg['net_weight']} kg",
            f"{pkg['gross_weight']} kg",
            pkg.get("dimensions", ""),
        ])

    t2 = Table(pkg_data, colWidths=[15*mm, 55*mm, 15*mm, 30*mm, 32*mm, 43*mm])
    t2.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
    ]))
    elements.append(t2)
    elements.append(Spacer(1, 3 * mm))

    # Totals
    totals = [
        ["", "", f"Total Pkgs: {data['total_packages']}",
         f"Total Net: {data['total_net_weight']:.1f} kg",
         f"Total Gross: {data['total_gross_weight']:.1f} kg",
         ""],
    ]
    t3 = Table(totals, colWidths=[15*mm, 55*mm, 15*mm, 30*mm, 32*mm, 43*mm])
    t3.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("LINEABOVE", (0, 0), (-1, 0), 1, colors.black),
    ]))
    elements.append(t3)

    doc.build(elements)


def _add_scan_noise(pdf_path: Path, output_path: Path, rotate: int = 0) -> None:
    """
    Convert a PDF page to an image and add scanning artefacts:
    - Slight rotation (simulates misaligned scanner)
    - Gaussian noise
    - Mild blur

    Saves as PNG (so the OCR parser is used in the pipeline).
    """
    import fitz
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    mat = fitz.Matrix(2.0, 2.0)  # 2x zoom = ~144 DPI
    pix = page.get_pixmap(matrix=mat, alpha=False)
    doc.close()

    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    # Rotate slightly if requested
    if rotate:
        img = img.rotate(rotate, expand=False, fillcolor=(255, 255, 255))

    # Add noise by drawing random grey pixels
    draw = ImageDraw.Draw(img)
    for _ in range(int(img.width * img.height * 0.001)):  # 0.1% of pixels
        x = random.randint(0, img.width - 1)
        y = random.randint(0, img.height - 1)
        grey = random.randint(150, 220)
        draw.point((x, y), fill=(grey, grey, grey))

    # Mild blur (simulates slight focus issues)
    img = img.filter(ImageFilter.GaussianBlur(radius=0.5))

    img.save(str(output_path), "PNG", dpi=(150, 150))


# ── Document generators ───────────────────────────────────────────────────────

def generate_bol(idx: int, split: str, introduce_issues: list[str] = None) -> tuple[dict, GroundTruth]:
    """Generate one Bill of Lading dataset entry."""
    issues = introduce_issues or []

    shipper_idx = idx % len(COMPANIES)
    consignee_idx = (idx + 3) % len(COMPANIES)
    vessel, voyage = VESSELS[idx % len(VESSELS)]

    containers = [random_container() for _ in range(random.randint(1, 3))]
    seals = [random_seal() for _ in containers]
    pkg_count = random.randint(50, 500)
    gross_weight = round(random.uniform(5000, 25000), 1)

    consignee_name = COMPANIES[consignee_idx]
    # Introduce wrong consignee mismatch
    if "wrong_consignee" in issues:
        consignee_name = random.choice([c for c in COMPANIES if c != COMPANIES[consignee_idx]])

    data = {
        "bl_number": f"BL{2024000 + idx:06d}",
        "date_of_issue": str(random_date()),
        "shipper_name": COMPANIES[shipper_idx],
        "shipper_address": ADDRESSES[shipper_idx],
        "consignee_name": consignee_name,
        "consignee_address": ADDRESSES[consignee_idx],
        "notify_party": COMPANIES[(consignee_idx + 1) % len(COMPANIES)],
        "vessel": vessel,
        "voyage": voyage,
        "port_of_loading": PORTS[idx % len(PORTS)],
        "port_of_discharge": PORTS[(idx + 4) % len(PORTS)],
        "container_numbers": containers,
        "seal_numbers": seals,
        "packages_count": pkg_count,
        "gross_weight": gross_weight,
        "weight_unit": "KG",
        "goods_description": random.choice([
            "ELECTRONIC COMPONENTS",
            "TEXTILE GARMENTS",
            "PLASTIC HOUSEHOLD GOODS",
            "AUTOMOTIVE PARTS",
            "CONSUMER ELECTRONICS",
        ]),
        "freight_terms": random.choice(FREIGHT_TERMS),
        "incoterm": random.choice(INCOTERMS),
    }

    # Remove a required field if testing missing-field validation
    if "missing_bl_number" in issues:
        data["bl_number"] = ""

    gt = GroundTruth(
        doc_type="bill_of_lading",
        split=split,
        expected_fields={
            "bl_number": data["bl_number"],
            "vessel": data["vessel"],
            "port_of_loading": data["port_of_loading"],
            "port_of_discharge": data["port_of_discharge"],
            "gross_weight": str(data["gross_weight"]),
            "weight_unit": data["weight_unit"],
        },
        expected_routing="exception_queue" if issues else "auto_approve",
        issues=issues,
    )
    return data, gt


def generate_invoice(
    idx: int,
    split: str,
    po_number: str,
    po_total: float,
    currency: str,
    introduce_issues: list[str] = None,
) -> tuple[dict, GroundTruth]:
    """Generate one Commercial Invoice, linked to a PO."""
    issues = introduce_issues or []

    n_items = random.randint(2, 5)
    line_items = []
    subtotal = 0.0

    for j in range(n_items):
        qty = random.randint(10, 200)
        unit_price = round(random.uniform(5.0, 500.0), 2)
        line_total = round(qty * unit_price, 2)
        subtotal += line_total
        line_items.append({
            "description": f"Item {j+1}: {random.choice(['Electronic Module', 'Textile Roll', 'Plastic Component', 'Steel Part', 'Circuit Board'])}",
            "hs_code": random.choice(HS_CODES),
            "quantity": qty,
            "unit_price": unit_price,
            "line_total": line_total,
        })

    subtotal = round(subtotal, 2)
    freight = round(random.uniform(100, 500), 2)
    actual_total = round(subtotal + freight, 2)

    # Introduce wrong total mismatch against PO
    if "wrong_total" in issues:
        # Make total 15% higher than PO (will trigger exception_queue)
        display_total = round(po_total * 1.15, 2)
    elif "small_mismatch" in issues:
        # Within 2% — will auto_approve
        display_total = round(po_total * 1.01, 2)
    elif "medium_mismatch" in issues:
        # Between 2-10% — manager_approval
        display_total = round(po_total * 1.06, 2)
    else:
        display_total = po_total  # exact match → auto_approve

    # Wrong math on line items if requested
    if "wrong_line_total" in issues:
        line_items[0]["line_total"] = round(line_items[0]["line_total"] * 1.5, 2)

    seller_idx = idx % len(COMPANIES)
    buyer_idx = (idx + 5) % len(COMPANIES)

    data = {
        "invoice_number": f"INV-{2024000 + idx:06d}",
        "invoice_date": str(random_date()),
        "seller_name": COMPANIES[seller_idx],
        "seller_address": ADDRESSES[seller_idx],
        "buyer_name": COMPANIES[buyer_idx],
        "buyer_address": ADDRESSES[buyer_idx],
        "currency": currency,
        "line_items": line_items,
        "subtotal": subtotal,
        "tax_or_freight": freight,
        "total_amount": display_total,
        "incoterm": random.choice(INCOTERMS),
        "payment_terms": random.choice(["NET 30", "NET 60", "LETTER OF CREDIT", "TT IN ADVANCE"]),
        "po_reference": po_number,
    }

    pct = abs(display_total - po_total) / po_total * 100 if po_total else 0
    if "wrong_total" in issues:
        expected_routing = "exception_queue"
    elif "medium_mismatch" in issues:
        expected_routing = "manager_approval"
    else:
        expected_routing = "auto_approve"

    if issues and "wrong_total" not in issues and "medium_mismatch" not in issues and "small_mismatch" not in issues:
        expected_routing = "exception_queue"

    gt = GroundTruth(
        doc_type="commercial_invoice",
        split=split,
        expected_fields={
            "invoice_number": data["invoice_number"],
            "currency": data["currency"],
            "total_amount": str(data["total_amount"]),
            "po_reference": data["po_reference"],
        },
        expected_routing=expected_routing,
        issues=issues,
        po_number=po_number,
        po_total=po_total,
        po_currency=currency,
    )
    return data, gt


def generate_packing_list(
    idx: int,
    split: str,
    introduce_issues: list[str] = None,
) -> tuple[dict, GroundTruth]:
    """Generate one Packing List."""
    issues = introduce_issues or []

    n_pkgs = random.randint(3, 8)
    packages = []
    total_gross = 0.0
    total_net = 0.0

    for j in range(n_pkgs):
        net = round(random.uniform(20, 200), 1)
        gross = round(net * random.uniform(1.05, 1.15), 1)
        total_net += net
        total_gross += gross
        packages.append({
            "package_id": f"CTN-{j+1:03d}",
            "description": f"Carton {j+1}: Mixed goods",
            "quantity": random.randint(10, 50),
            "net_weight": net,
            "gross_weight": gross,
            "dimensions": f"{random.randint(40,80)}x{random.randint(30,60)}x{random.randint(20,50)} cm",
        })

    # Introduce weight mismatch
    if "wrong_weight" in issues:
        total_gross = round(total_gross * 1.25, 1)  # total doesn't match sum

    shipper_idx = idx % len(COMPANIES)
    consignee_idx = (idx + 2) % len(COMPANIES)

    data = {
        "pl_number": f"PL-{2024000 + idx:06d}",
        "date": str(random_date()),
        "shipper_name": COMPANIES[shipper_idx],
        "consignee_name": COMPANIES[consignee_idx],
        "packages": packages,
        "total_packages": n_pkgs,
        "total_net_weight": round(total_net, 1),
        "total_gross_weight": round(total_gross, 1),
    }

    expected_routing = "exception_queue" if issues else "auto_approve"

    gt = GroundTruth(
        doc_type="packing_list",
        split=split,
        expected_fields={
            "packing_list_number": data["pl_number"],
            "total_packages": str(data["total_packages"]),
            "total_gross_weight": str(data["total_gross_weight"]),
        },
        expected_routing=expected_routing,
        issues=issues,
    )
    return data, gt


def save_po(po_number: str, po_total: float, currency: str, supplier: str, idx: int) -> None:
    """Save a Purchase Order JSON for cross-document validation."""
    po = {
        "po_number": po_number,
        "supplier_name": supplier,
        "total_amount": po_total,
        "currency": currency,
        "issue_date": str(random_date()),
        "quantity": random.randint(100, 1000),
        "quantity_unit": "units",
    }
    path = PO_DIR / f"po_{po_number.replace('-', '_').lower()}.json"
    path.write_text(json.dumps(po, indent=2))


def save_document(
    data: dict,
    gt: GroundTruth,
    pdf_func,
    basename: str,
    noisy: bool = False,
    noise_rotate: int = 0,
) -> None:
    """Generate a PDF (and optionally a noisy PNG scan), save with ground truth."""
    split_dir = TRAIN_DIR if gt.split == "train" else TEST_DIR

    pdf_path = split_dir / f"{basename}.pdf"
    gt_path = split_dir / f"{basename}.json"

    if REPORTLAB_AVAILABLE:
        pdf_func(pdf_path, data)
    else:
        # Fallback: write a placeholder text file if reportlab isn't installed
        pdf_path.with_suffix(".txt").write_text(
            f"[Placeholder — install reportlab to generate real PDFs]\n{json.dumps(data, indent=2)}"
        )

    # Save ground-truth label
    gt_path.write_text(json.dumps({
        "doc_type": gt.doc_type,
        "split": gt.split,
        "expected_fields": gt.expected_fields,
        "expected_routing": gt.expected_routing,
        "issues": gt.issues,
        "po_number": gt.po_number,
        "po_total": gt.po_total,
        "po_currency": gt.po_currency,
        "filename": f"{basename}.pdf",
    }, indent=2))

    # Optionally generate a noisy scanned version
    if noisy and REPORTLAB_AVAILABLE and pdf_path.exists():
        scan_path = split_dir / f"{basename}_scan.png"
        noisy_gt_path = split_dir / f"{basename}_scan.json"
        _add_scan_noise(pdf_path, scan_path, rotate=noise_rotate)
        noisy_gt = dict(json.loads(gt_path.read_text()))
        noisy_gt["filename"] = f"{basename}_scan.png"
        noisy_gt["scan_conditions"] = {"rotation": noise_rotate, "noise": True}
        noisy_gt_path.write_text(json.dumps(noisy_gt, indent=2))


def main() -> None:
    if not REPORTLAB_AVAILABLE:
        print("⚠️  reportlab not installed — PDFs will be text placeholders.")
        print("   Install with: pip install reportlab")

    print("\n📦 Generating synthetic shipping documents...\n")

    generated = {"train": 0, "test": 0}

    # ── Train split: 30 documents ─────────────────────────────────────────────
    # 10 Bills of Lading (6 clean, 2 noisy, 1 missing field, 1 wrong consignee)
    for i in range(6):
        data, gt = generate_bol(i, "train")
        save_document(data, gt, _make_pdf_bol, f"bol_train_{i:02d}")
        generated["train"] += 1

    for i in range(2):
        data, gt = generate_bol(10 + i, "train")
        save_document(data, gt, _make_pdf_bol, f"bol_train_scan_{i:02d}", noisy=True, noise_rotate=random.choice([2, -3, 1]))
        generated["train"] += 1

    data, gt = generate_bol(20, "train", introduce_issues=["missing_bl_number"])
    save_document(data, gt, _make_pdf_bol, "bol_train_missing_bl")
    generated["train"] += 1

    data, gt = generate_bol(21, "train", introduce_issues=["wrong_consignee"])
    save_document(data, gt, _make_pdf_bol, "bol_train_wrong_consignee")
    generated["train"] += 1

    # 10 Commercial Invoices (4 auto_approve, 2 manager_approval, 2 exception, 2 math errors)
    currencies = ["USD", "USD", "EUR", "USD", "EUR", "USD", "EUR", "USD", "USD", "EUR"]
    po_totals = [15250, 8400, 22100, 45000, 6750, 12000, 9800, 31000, 18500, 7200]
    invoice_scenarios = [
        [],                    # exact match → auto_approve
        [],                    # exact match → auto_approve
        ["small_mismatch"],    # within 2% → auto_approve
        ["small_mismatch"],    # within 2% → auto_approve
        ["medium_mismatch"],   # 2-10% → manager_approval
        ["medium_mismatch"],   # 2-10% → manager_approval
        ["wrong_total"],       # >10% → exception_queue
        ["wrong_total"],       # >10% → exception_queue
        ["wrong_line_total"],  # math error → exception_queue
        ["wrong_line_total"],  # math error → exception_queue
    ]

    for i in range(10):
        po_num = random_po_number()
        currency = currencies[i]
        po_total = po_totals[i]
        supplier = COMPANIES[i % len(COMPANIES)]
        save_po(po_num, po_total, currency, supplier, i)

        data, gt = generate_invoice(
            100 + i, "train", po_num, po_total, currency,
            introduce_issues=invoice_scenarios[i]
        )
        save_document(data, gt, _make_pdf_invoice, f"invoice_train_{i:02d}")
        generated["train"] += 1

    # 10 Packing Lists (7 clean, 2 noisy, 1 weight mismatch)
    for i in range(7):
        data, gt = generate_packing_list(200 + i, "train")
        save_document(data, gt, _make_pdf_packing, f"pl_train_{i:02d}")
        generated["train"] += 1

    for i in range(2):
        data, gt = generate_packing_list(210 + i, "train")
        save_document(data, gt, _make_pdf_packing, f"pl_train_scan_{i:02d}", noisy=True, noise_rotate=random.choice([1, -2]))
        generated["train"] += 1

    data, gt = generate_packing_list(220, "train", introduce_issues=["wrong_weight"])
    save_document(data, gt, _make_pdf_packing, "pl_train_wrong_weight")
    generated["train"] += 1

    # ── Test split: 10 documents (held out — do not tune prompts on these) ────
    # 3 BOLs, 4 invoices, 3 packing lists — mix of conditions
    data, gt = generate_bol(50, "test")
    save_document(data, gt, _make_pdf_bol, "bol_test_00")
    generated["test"] += 1

    data, gt = generate_bol(51, "test", introduce_issues=["wrong_consignee"])
    save_document(data, gt, _make_pdf_bol, "bol_test_01")
    generated["test"] += 1

    data, gt = generate_bol(52, "test")
    save_document(data, gt, _make_pdf_bol, "bol_test_scan_00", noisy=True, noise_rotate=3)
    generated["test"] += 1

    for i, scenario in enumerate([[], ["medium_mismatch"], ["wrong_total"], ["small_mismatch"]]):
        po_num = random_po_number()
        po_total = random.choice([10000, 20000, 35000, 50000])
        save_po(po_num, po_total, "USD", COMPANIES[i], 50 + i)
        data, gt = generate_invoice(150 + i, "test", po_num, po_total, "USD", introduce_issues=scenario)
        save_document(data, gt, _make_pdf_invoice, f"invoice_test_{i:02d}")
        generated["test"] += 1

    data, gt = generate_packing_list(250, "test")
    save_document(data, gt, _make_pdf_packing, "pl_test_00")
    generated["test"] += 1

    data, gt = generate_packing_list(251, "test", introduce_issues=["wrong_weight"])
    save_document(data, gt, _make_pdf_packing, "pl_test_01")
    generated["test"] += 1

    data, gt = generate_packing_list(252, "test")
    save_document(data, gt, _make_pdf_packing, "pl_test_scan_00", noisy=True)
    generated["test"] += 1

    print(f"  ✓ Train split: {generated['train']} documents in data/synthetic/train/")
    print(f"  ✓ Test split:  {generated['test']} documents in data/synthetic/test/")
    print(f"  ✓ POs:         {len(list(PO_DIR.iterdir()))} purchase orders in data/synthetic/pos/")
    print("\n⚠️  TEST SPLIT IS HELD OUT — do not use for prompt tuning.\n")

    # Write a manifest
    manifest = {
        "train_count": generated["train"],
        "test_count": generated["test"],
        "total": generated["train"] + generated["test"],
        "note": "Test split is held out. Never tune prompts on test documents.",
        "splits": {
            "train": [f.name for f in sorted(TRAIN_DIR.glob("*.json"))],
            "test": [f.name for f in sorted(TEST_DIR.glob("*.json"))],
        }
    }
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"  ✓ Manifest saved to data/synthetic/manifest.json")


if __name__ == "__main__":
    main()
