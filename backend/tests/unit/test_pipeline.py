"""
Unit tests for pipeline nodes using a mocked LLM client.

We mock get_llm_client() to return a fake client so these tests:
  - Run without an API key
  - Run without Docker/DB (DB calls are also mocked)
  - Run in milliseconds
  - Are deterministic

This is the correct way to test LLM-based code: test the pipeline logic
(schema parsing, retry decisions, grounding integration) not the LLM itself.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from app.parsers.types import ParsedDocument, ParsedPage, WordBox
from app.pipeline.state import PipelineState
from app.schemas.extraction import (
    BillOfLadingSchema,
    ClassificationResult,
    CommercialInvoiceSchema,
    DocumentType,
    Evidence,
    ExtractedField,
    FieldStatus,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_state(doc_type: str = "bill_of_lading") -> PipelineState:
    state = PipelineState(
        document_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        storage_path="/fake/path/doc.pdf",
    )
    state.parsed_document = ParsedDocument(
        parser_used="pymupdf",
        pages=[
            ParsedPage(
                page_number=1,
                width=595.0,
                height=842.0,
                raw_text=(
                    "BILL OF LADING\n"
                    "B/L Number: BL2024001234\n"
                    "Vessel: MSC AURORA\n"
                    "Port of Loading: SHANGHAI\n"
                    "Port of Discharge: LOS ANGELES\n"
                    "Gross Weight: 15250 KG\n"
                ),
                words=[
                    WordBox("BILL", 10, 10, 50, 20, 1),
                    WordBox("OF", 55, 10, 75, 20, 1),
                    WordBox("LADING", 80, 10, 130, 20, 1),
                    WordBox("BL2024001234", 10, 30, 120, 40, 1),
                    WordBox("MSC", 10, 50, 45, 60, 1),
                    WordBox("AURORA", 50, 50, 110, 60, 1),
                    WordBox("SHANGHAI", 10, 70, 90, 80, 1),
                    WordBox("LOS", 10, 90, 40, 100, 1),
                    WordBox("ANGELES", 45, 90, 110, 100, 1),
                    WordBox("15250", 10, 110, 55, 120, 1),
                    WordBox("KG", 60, 110, 80, 120, 1),
                ],
            )
        ],
        total_words=11,
    )
    state.classification = ClassificationResult(
        doc_type=DocumentType(doc_type),
        confidence=0.97,
        reasoning="Document contains vessel name and BL number.",
    )
    return state


def make_mock_llm_client(response_content: str):
    """Create a mock LLM client that returns the given JSON content."""
    from app.llm.provider import LLMResponse
    mock_client = MagicMock()
    mock_client.complete.return_value = LLMResponse(
        content=response_content,
        model="gpt-4o-mini",
        prompt_tokens=500,
        completion_tokens=200,
        total_tokens=700,
        cost_usd=0.0002,
        latency_ms=1200,
    )
    return mock_client


# ── LLM Provider tests ────────────────────────────────────────────────────────

def test_estimate_cost_known_model():
    pytest.importorskip("structlog", reason="structlog not installed — runs in Docker")
    from app.llm.provider import estimate_cost
    cost = estimate_cost("gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert cost == pytest.approx(0.75, rel=0.01)


def test_estimate_cost_unknown_model():
    pytest.importorskip("structlog", reason="structlog not installed — runs in Docker")
    from app.llm.provider import estimate_cost
    cost = estimate_cost("unknown-model-xyz", 1000, 500)
    assert cost == 0.0


def test_llm_response_defaults():
    pytest.importorskip("structlog", reason="structlog not installed — runs in Docker")
    from app.llm.provider import LLMResponse
    r = LLMResponse(content="hello", model="gpt-4o-mini")
    assert r.prompt_tokens == 0
    assert r.cost_usd == 0.0


# ── Classify node tests ───────────────────────────────────────────────────────

def test_classify_keyword_fallback():
    pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
    from app.pipeline.nodes.classify import _classify_by_keywords
    text = "BILL OF LADING - BL Number: BL123 - Vessel: MSC AURORA - Port of Loading: SHANGHAI"
    result = _classify_by_keywords(text)
    assert result == DocumentType.BILL_OF_LADING


def test_classify_keyword_invoice():
    pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
    from app.pipeline.nodes.classify import _classify_by_keywords
    text = "COMMERCIAL INVOICE - Invoice No: INV-001 - Seller: Acme Corp - Unit Price: USD 150.00 - HS Code: 8471.30"
    result = _classify_by_keywords(text)
    assert result == DocumentType.COMMERCIAL_INVOICE


def test_classify_keyword_packing():
    pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
    from app.pipeline.nodes.classify import _classify_by_keywords
    text = "PACKING LIST - Total Packages: 50 - Net Weight: 1200 kg - Gross Weight: 1350 kg - Dimensions: 40x30x20 cm"
    result = _classify_by_keywords(text)
    assert result == DocumentType.PACKING_LIST


def test_classify_keyword_unknown():
    pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
    from app.pipeline.nodes.classify import _classify_by_keywords
    text = "Random document with no relevant logistics keywords"
    result = _classify_by_keywords(text)
    assert result == DocumentType.UNKNOWN


def test_classify_node_with_mock_llm():
    pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
    pytest.importorskip("structlog", reason="structlog not installed — runs in Docker")
    from app.pipeline.nodes.classify import run_classify_node

    state = make_state()
    mock_llm_response = json.dumps({
        "doc_type": "bill_of_lading",
        "confidence": 0.97,
        "reasoning": "Contains BL number and vessel information.",
    })

    mock_db = MagicMock()
    mock_db.execute.return_value.scalars.return_value.all.return_value = []

    with patch("app.pipeline.nodes.classify.get_llm_client", return_value=make_mock_llm_client(mock_llm_response)):
        with patch("app.pipeline.nodes.classify.render_prompt", return_value="classify this document"):
            result_state = run_classify_node(state, mock_db)

    assert result_state.classification is not None
    assert result_state.classification.doc_type == "bill_of_lading"
    assert result_state.classification.confidence == pytest.approx(0.97)
    assert result_state.error is None


# ── Extract node tests ────────────────────────────────────────────────────────

def test_extract_node_with_mock_llm():
    pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
    pytest.importorskip("structlog", reason="structlog not installed — runs in Docker")
    from app.pipeline.nodes.extract import run_extract_node

    state = make_state("bill_of_lading")
    mock_extraction = {
        "bl_number": {
            "value": "BL2024001234",
            "confidence": 0.98,
            "evidence": {"page": 1, "snippet": "B/L Number: BL2024001234"},
            "status": "ungrounded",
        },
        "vessel": {
            "value": "MSC AURORA",
            "confidence": 0.96,
            "evidence": {"page": 1, "snippet": "Vessel: MSC AURORA"},
            "status": "ungrounded",
        },
        "port_of_loading": {
            "value": "SHANGHAI",
            "confidence": 0.95,
            "evidence": {"page": 1, "snippet": "Port of Loading: SHANGHAI"},
            "status": "ungrounded",
        },
    }

    mock_db = MagicMock()
    mock_db.execute.return_value.scalars.return_value.all.return_value = []
    mock_db.flush = MagicMock()

    with patch("app.pipeline.nodes.extract.get_llm_client", return_value=make_mock_llm_client(json.dumps(mock_extraction))):
        with patch("app.pipeline.nodes.extract.render_prompt", return_value="extract this"):
            result_state = run_extract_node(state, mock_db)

    assert result_state.error is None
    assert result_state.extraction_bol is not None
    assert result_state.extraction_bol.bl_number.value == "BL2024001234"
    assert result_state.extraction_bol.vessel.value == "MSC AURORA"
    assert result_state.total_tokens == 700
    assert result_state.total_cost_usd == pytest.approx(0.0002)


def test_extract_node_invalid_json_sets_error():
    pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
    pytest.importorskip("structlog", reason="structlog not installed — runs in Docker")
    from app.pipeline.nodes.extract import run_extract_node

    state = make_state("bill_of_lading")
    mock_db = MagicMock()

    with patch("app.pipeline.nodes.extract.get_llm_client", return_value=make_mock_llm_client("not valid json {")):
        with patch("app.pipeline.nodes.extract.render_prompt", return_value="extract"):
            result_state = run_extract_node(state, mock_db)

    assert result_state.error is not None
    assert "Extraction failed" in result_state.error


# ── Retry logic tests ─────────────────────────────────────────────────────────

def test_should_retry_when_ungrounded_fields():
    pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
    from app.pipeline.nodes.retry_extract import should_retry
    state = make_state()
    state.fields_to_retry = [{"field_name": "bl_number", "reason": "ungrounded"}]
    assert should_retry(state) is True


def test_should_not_retry_at_max_retries():
    pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
    from app.pipeline.nodes.retry_extract import should_retry, MAX_RETRIES
    state = make_state()
    state.fields_to_retry = [{"field_name": "bl_number", "reason": "ungrounded"}]
    state.extraction_attempt = MAX_RETRIES
    assert should_retry(state) is False


def test_should_not_retry_when_no_issues():
    pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
    from app.pipeline.nodes.retry_extract import should_retry
    state = make_state()
    state.fields_to_retry = []
    state.validation_errors = []
    assert should_retry(state) is False


def test_should_not_retry_when_error():
    pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
    from app.pipeline.nodes.retry_extract import should_retry
    state = make_state()
    state.fields_to_retry = [{"field_name": "x", "reason": "y"}]
    state.error = "Something went wrong"
    assert should_retry(state) is False


# ── Ground node unit tests ────────────────────────────────────────────────────

def test_ground_node_updates_state():
    pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
    from app.pipeline.nodes.ground import run_ground_node

    state = make_state("bill_of_lading")
    state.extraction_bol = BillOfLadingSchema(
        bl_number=ExtractedField(
            value="BL2024001234",
            confidence=0.98,
            evidence=Evidence(page=1, snippet="BL2024001234"),
        ),
        vessel=ExtractedField(
            value="MSC AURORA",
            confidence=0.96,
            evidence=Evidence(page=1, snippet="MSC AURORA"),
        ),
    )

    mock_pages = [
        MagicMock(
            page_number=1,
            words={"words": [
                {"text": w.text, "x0": w.x0, "y0": w.y0, "x1": w.x1, "y1": w.y1, "page": w.page}
                for w in state.parsed_document.pages[0].words
            ]}
        )
    ]
    mock_db = MagicMock()
    mock_db.execute.return_value.scalars.return_value.all.return_value = mock_pages

    result_state = run_ground_node(state, mock_db)

    assert result_state.error is None
    assert result_state.grounding_rate >= 0.0
    assert result_state.overall_confidence >= 0.0
