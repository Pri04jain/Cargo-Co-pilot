"""
Unit tests for the RAG components:
  - Text chunker: sentence splitting, chunk size, overlap
  - Chat service: refusal when context is empty, citation parsing
  - Retrieval ranking: merge logic
"""

import pytest
from app.rag.chunker import TextChunk, chunk_document, _split_sentences, _pack_sentences


# ── Chunker ───────────────────────────────────────────────────────────────────

class TestChunker:

    def test_empty_input(self):
        result = chunk_document([])
        assert result == []

    def test_empty_text_skipped(self):
        result = chunk_document([(1, ""), (2, "   ")])
        assert result == []

    def test_single_short_page(self):
        text = "This is a shipping document. It has a bill of lading number."
        result = chunk_document([(1, text)], max_tokens=500)
        assert len(result) == 1
        assert result[0].page_number == 1
        assert result[0].chunk_index == 0
        assert text.split()[0] in result[0].text

    def test_chunk_respects_page_number(self):
        pages = [
            (1, "Page one text about the shipper and consignee."),
            (2, "Page two text about the vessel and port of loading."),
        ]
        result = chunk_document(pages, max_tokens=500)
        page_numbers = [c.page_number for c in result]
        assert 1 in page_numbers
        assert 2 in page_numbers

    def test_chunk_index_is_global(self):
        """chunk_index should be sequential across all pages, not reset per page."""
        pages = [
            (1, "First page has some text. It is short."),
            (2, "Second page has other text. Also short."),
        ]
        result = chunk_document(pages, max_tokens=500)
        indices = [c.chunk_index for c in result]
        assert indices == list(range(len(result)))

    def test_long_text_split_into_multiple_chunks(self):
        # Use realistic text with sentence boundaries so the splitter can split
        sentences = [
            f"Line item {i} has a quantity of {i*10} units at USD {i*5:.2f} each."
            for i in range(1, 40)
        ]
        text = " ".join(sentences)
        result = chunk_document([(1, text)], max_tokens=50, overlap_tokens=10)
        assert len(result) > 1, f"Expected multiple chunks, got {len(result)}"

    def test_overlap_between_chunks(self):
        """Adjacent chunks should share some words due to the overlap window."""
        sentences = [f"Sentence number {i} about logistics documents." for i in range(20)]
        text = " ".join(sentences)
        result = chunk_document([(1, text)], max_tokens=50, overlap_tokens=15)
        if len(result) >= 2:
            # Last few words of chunk 0 should appear at the start of chunk 1
            words_end_of_0 = set(result[0].text.split()[-5:])
            words_start_of_1 = set(result[1].text.split()[:10])
            assert words_end_of_0 & words_start_of_1, "No overlap found between adjacent chunks"


class TestSentenceSplitter:

    def test_splits_on_period_uppercase(self):
        text = "The vessel is MSC AURORA. Port of loading is SHANGHAI."
        sentences = _split_sentences(text)
        assert len(sentences) >= 2

    def test_does_not_split_decimal(self):
        text = "The total is USD 15,250.00 as per the invoice."
        sentences = _split_sentences(text)
        # Should not split on the decimal point
        assert len(sentences) == 1

    def test_splits_on_double_newline(self):
        text = "First paragraph.\n\nSecond paragraph."
        sentences = _split_sentences(text)
        assert len(sentences) == 2

    def test_empty_string(self):
        assert _split_sentences("") == []

    def test_single_sentence(self):
        result = _split_sentences("Just one sentence here.")
        assert len(result) == 1


# ── Chat service ──────────────────────────────────────────────────────────────

class TestChatService:

    def test_refusal_when_no_chunks(self):
        pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
        """If retrieval returns nothing, we should get the refusal response."""
        from app.rag.chat_service import (
            REFUSAL_RESPONSE, ChatResponse, _parse_llm_response
        )
        # Empty retrieval → should return refusal without calling LLM
        # We test _parse_llm_response with INSUFFICIENT_CONTEXT signal
        result = _parse_llm_response(
            raw="INSUFFICIENT_CONTEXT",
            chunks=[],
            tokens=0,
            cost=0.0,
        )
        assert result.sufficient_context is False
        assert result.answer == REFUSAL_RESPONSE

    def test_parses_valid_json_response(self):
        pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
        from app.rag.chat_service import _parse_llm_response, Citation
        from unittest.mock import MagicMock
        import uuid

        mock_chunk = MagicMock()
        mock_chunk.document_id = uuid.uuid4()
        mock_chunk.page_number = 1
        mock_chunk.original_filename = "invoice_001.pdf"
        mock_chunk.text = "Invoice total: USD 15,250.00"
        mock_chunk.score = 0.92

        raw = '''{"answer": "The invoice total is USD 15,250.00.", "citations": [{"document_id": "abc123", "page_number": 1, "filename": "invoice_001.pdf", "snippet": "Invoice total: USD 15,250.00"}], "sufficient_context": true}'''

        result = _parse_llm_response(raw=raw, chunks=[mock_chunk], tokens=500, cost=0.0002)
        assert result.sufficient_context is True
        assert "15,250.00" in result.answer
        assert len(result.citations) == 1
        assert result.citations[0].filename == "invoice_001.pdf"

    def test_handles_malformed_json_gracefully(self):
        pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
        from app.rag.chat_service import _parse_llm_response
        from unittest.mock import MagicMock
        mock_chunk = MagicMock()
        mock_chunk.document_id = "some-id"
        mock_chunk.page_number = 1
        mock_chunk.original_filename = "test.pdf"
        mock_chunk.text = "test text"

        result = _parse_llm_response(
            raw="The total is 15,000 USD (this is not JSON)",
            chunks=[mock_chunk],
            tokens=100,
            cost=0.0,
        )
        assert result.answer is not None
        assert len(result.answer) > 0

    def test_builds_context_block_with_citations(self):
        pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
        from app.rag.chat_service import _build_context_block
        from unittest.mock import MagicMock
        import uuid

        chunks = []
        for i in range(2):
            c = MagicMock()
            c.document_id = uuid.uuid4()
            c.original_filename = f"doc_{i}.pdf"
            c.page_number = i + 1
            c.text = f"Text for chunk {i}"
            chunks.append(c)

        block = _build_context_block(chunks)
        assert "doc_0.pdf" in block
        assert "doc_1.pdf" in block
        assert "Page: 1" in block
        assert "Page: 2" in block

    def test_strips_markdown_fences(self):
        pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
        from app.rag.chat_service import _parse_llm_response
        from unittest.mock import MagicMock

        raw = '```json\n{"answer": "hello", "citations": [], "sufficient_context": true}\n```'
        mock_chunk = MagicMock()
        mock_chunk.document_id = "id1"
        mock_chunk.page_number = 1
        mock_chunk.original_filename = "f.pdf"
        mock_chunk.text = "t"

        result = _parse_llm_response(raw=raw, chunks=[mock_chunk], tokens=10, cost=0.0)
        assert result.answer == "hello"
        assert result.sufficient_context is True


# ── Retrieval merge logic ─────────────────────────────────────────────────────

class TestRetrievalMerge:

    def make_chunk(self, doc_id, page, text, score, sql_matched=False):
        pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
        from app.rag.retriever import RetrievedChunk
        import uuid
        return RetrievedChunk(
            chunk_id=uuid.uuid4(),
            document_id=uuid.UUID(doc_id) if len(doc_id) == 36 else uuid.uuid4(),
            original_filename="test.pdf",
            page_number=page,
            text=text,
            score=score,
            sql_matched=sql_matched,
        )

    def test_deduplicates_same_chunk(self):
        pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
        from app.rag.retriever import _merge
        import uuid
        doc_id = str(uuid.uuid4())

        sql = [self.make_chunk(doc_id, 1, "Invoice total 15000", 0.9, True)]
        vec = [self.make_chunk(doc_id, 1, "Invoice total 15000", 0.7, False)]

        result = _merge(sql, vec, top_k=5)
        assert len(result) == 1

    def test_sql_boost_applied(self):
        pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
        from app.rag.retriever import _merge, SQL_SCORE_BOOST
        import uuid
        doc_id = str(uuid.uuid4())

        sql = [self.make_chunk(doc_id, 1, "Invoice total 15000", 0.9, True)]
        vec_chunk = self.make_chunk(doc_id, 2, "Port of loading SHANGHAI", 0.6, False)

        result = _merge(sql, [vec_chunk], top_k=5)
        boosted = next((c for c in result if "SHANGHAI" in c.text), None)
        if boosted:
            assert boosted.score >= 0.6 + SQL_SCORE_BOOST or boosted.sql_matched

    def test_sorted_by_score_descending(self):
        pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
        from app.rag.retriever import _merge
        import uuid

        vec = [
            self.make_chunk(str(uuid.uuid4()), 1, f"text chunk {i} unique", 0.9 - i * 0.1)
            for i in range(4)
        ]
        result = _merge([], vec, top_k=4)
        scores = [c.score for c in result]
        assert scores == sorted(scores, reverse=True)

    def test_respects_top_k(self):
        pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed — runs in Docker")
        from app.rag.retriever import _merge
        import uuid

        vec = [
            self.make_chunk(str(uuid.uuid4()), 1, f"text chunk {i} unique", 0.9 - i * 0.05)
            for i in range(10)
        ]
        result = _merge([], vec, top_k=3)
        assert len(result) == 3
