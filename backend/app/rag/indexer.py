"""
Chunk indexer — called by the finalize pipeline node to:
  1. Chunk each page's text
  2. Embed the chunks
  3. Store chunks + embeddings in the chunks table

This runs synchronously inside the Celery worker after the rest of the pipeline
completes. It's the last step before the document becomes searchable.

Idempotent: deletes existing chunks for the document before re-indexing,
so a pipeline retry doesn't create duplicate chunks.
"""

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.chunk import Chunk
from app.rag.chunker import chunk_document
from app.rag.embedder import embed_texts

logger = get_logger(__name__)


def index_document(
    db: Session,
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    pages: list[tuple[int, str]],           # [(page_number, raw_text)]
    field_context: Optional[dict] = None,   # key extracted fields for hybrid retrieval
) -> int:
    """
    Chunk, embed, and store all text chunks for a document.

    Returns the number of chunks created.
    """
    from sqlalchemy import delete

    # Remove any existing chunks (idempotent)
    db.execute(
        delete(Chunk).where(
            Chunk.document_id == document_id,
            Chunk.tenant_id == tenant_id,
        )
    )

    # Chunk the text
    chunks = chunk_document(pages)
    if not chunks:
        logger.warning("no_chunks_created", document_id=str(document_id))
        return 0

    # Embed all chunk texts in one batch
    texts = [c.text for c in chunks]
    vectors = embed_texts(texts)

    # Store in DB
    for chunk, vector in zip(chunks, vectors):
        db.add(Chunk(
            document_id=document_id,
            tenant_id=tenant_id,
            page_number=chunk.page_number,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            embedding=vector if any(v != 0.0 for v in vector) else None,
            field_context=field_context,
        ))

    db.commit()

    logger.info(
        "document_indexed",
        document_id=str(document_id),
        chunks=len(chunks),
        has_embeddings=vectors[0][0] != 0.0 if vectors else False,
    )
    return len(chunks)
