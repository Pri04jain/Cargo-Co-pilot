"""
Parse node — extracts text and bounding boxes from a document file.

Responsibilities:
  1. Call the correct parser (PDF or OCR) via the dispatcher
  2. Store each page's words in the document_pages table
  3. Update the document's page_count
  4. Populate state.parsed_document for downstream nodes

Why store pages in the DB at this stage?
  - Grounding node needs word boxes per page to match snippets
  - Ask Copilot needs page text for RAG chunking
  - Storing early means a failed extraction can be retried without re-parsing

This node is idempotent: if pages already exist for a document (from a prior
failed run), we delete and re-create them.
"""

import uuid

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.document import Document
from app.models.document import DocumentPage
from app.parsers.dispatcher import parse_document
from app.parsers.types import ParsedDocument
from app.pipeline.state import PipelineState
from app.services.audit import log_event_sync

logger = get_logger(__name__)


def run_parse_node(state: PipelineState, db: Session) -> PipelineState:
    """
    Parse node entry point. Called by the pipeline runner.

    Modifies state in-place and returns it (LangGraph convention).
    Sets state.error on failure so the finalize node can mark the document failed.
    """
    logger.info("parse_node_start", document_id=str(state.document_id))

    try:
        # ── Parse file ────────────────────────────────────────────────────────
        parsed: ParsedDocument = parse_document(state.storage_path)
        state.parsed_document = parsed

        # ── Persist pages ─────────────────────────────────────────────────────
        _persist_pages(db, state.document_id, state.tenant_id, parsed)

        # ── Update document page count ────────────────────────────────────────
        from sqlalchemy import select, update
        db.execute(
            update(Document)
            .where(Document.id == state.document_id)
            .values(page_count=len(parsed.pages))
        )

        log_event_sync(
            db,
            tenant_id=state.tenant_id,
            document_id=state.document_id,
            event_type="document_parsed",
            description=f"Parsed {len(parsed.pages)} page(s) using {parsed.parser_used}",
            metadata={
                "parser": parsed.parser_used,
                "pages": len(parsed.pages),
                "total_words": parsed.total_words,
            },
        )
        db.commit()

        logger.info(
            "parse_node_complete",
            document_id=str(state.document_id),
            pages=len(parsed.pages),
            words=parsed.total_words,
            parser=parsed.parser_used,
        )

    except Exception as exc:
        logger.exception("parse_node_failed", document_id=str(state.document_id), error=str(exc))
        state.error = f"Parse failed: {exc}"
        state.failed_node = "parse"

    return state


def _persist_pages(
    db: Session,
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    parsed: ParsedDocument,
) -> None:
    """
    Write parsed pages to the database.
    Deletes existing pages first (idempotent for retries).
    """
    from sqlalchemy import delete

    # Delete existing pages for this document (in case of retry)
    db.execute(
        delete(DocumentPage).where(DocumentPage.document_id == document_id)
    )

    for page in parsed.pages:
        db_page = DocumentPage(
            document_id=document_id,
            tenant_id=tenant_id,
            page_number=page.page_number,
            raw_text=page.raw_text,
            words={"words": [
                {
                    "text": w.text,
                    "x0": w.x0, "y0": w.y0,
                    "x1": w.x1, "y1": w.y1,
                    "page": w.page,
                }
                for w in page.words
            ]},
            width=page.width,
            height=page.height,
        )
        db.add(db_page)
