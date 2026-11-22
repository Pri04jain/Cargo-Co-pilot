"""
Hybrid retriever — combines structured SQL lookup with vector similarity search.

Strategy:
  1. SQL path: run a structured query over extracted_fields to find documents
     that match named entities or values in the query.
     Example: "invoice total on INV-001" → SQL: WHERE field_name='total_amount'
     AND document has 'INV-001' in invoice_number field.

  2. Vector path: embed the query, run cosine similarity search over chunks
     using pgvector's <=> operator, filtered to the current tenant.

  3. Merge: deduplicate, rank by relevance (vector score + SQL boost), return
     top-k results with document and page citations.

Why hybrid?
  - Structured questions ("What is the incoterm on invoice INV-001?") are best
    answered by SQL — exact match on extracted field values.
  - Freeform questions ("Are there shipments with weight mismatches?") benefit
    from semantic search over the raw document text.
  - Combining both covers the full question space with one retrieval call.

Tenant isolation:
  Every query is filtered by tenant_id — a tenant can only retrieve their own
  documents even if two tenants have identical document content.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.extraction import ExtractedField
from app.models.validation import ValidationResult
from app.rag.embedder import embed_query

logger = get_logger(__name__)

TOP_K = 5
SQL_SCORE_BOOST = 0.2
MIN_COSINE_SCORE = 0.15


@dataclass
class RetrievedChunk:
    """One retrieved piece of context with its citation info."""
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    original_filename: str
    page_number: int
    text: str
    score: float            # 0–1, higher = more relevant
    sql_matched: bool = False
    field_name: Optional[str] = None   # if a specific field was matched
    field_value: Optional[str] = None


def retrieve(
    db: Session,
    query: str,
    tenant_id: uuid.UUID,
    document_id: Optional[uuid.UUID] = None,  # scope to a single document if provided
    top_k: int = TOP_K,
) -> list[RetrievedChunk]:
    """
    Main retrieval function — returns ranked chunks for a query.

    Args:
        db: sync SQLAlchemy session (retrieval is called from sync chat handler)
        query: natural language question from the user
        tenant_id: scope all queries to this tenant
        document_id: optional — restrict to a single document
        top_k: maximum number of chunks to return
    """
    # ── SQL structured retrieval ──────────────────────────────────────────────
    sql_matches = _sql_field_lookup(db, query, tenant_id, document_id)

    # ── Validation results lookup (for queries about errors/warnings) ─────────
    validation_keywords = ["error", "warning", "fail", "invalid", "mismatch", "validation", "wrong", "missing"]
    if any(kw in query.lower() for kw in validation_keywords):
        sql_matches += _validation_lookup(db, tenant_id, document_id)

    # ── Vector similarity search ──────────────────────────────────────────────
    vector_matches = _vector_search(db, query, tenant_id, document_id, top_k * 2)

    # ── Merge and rank ────────────────────────────────────────────────────────
    merged = _merge(sql_matches, vector_matches, top_k)

    logger.debug(
        "retrieval_complete",
        query_len=len(query),
        sql_hits=len(sql_matches),
        vector_hits=len(vector_matches),
        merged=len(merged),
    )
    return merged


def _validation_lookup(
    db: Session,
    tenant_id: uuid.UUID,
    document_id: Optional[uuid.UUID],
) -> list[RetrievedChunk]:
    """
    Return validation failures as synthetic chunks so the LLM can answer
    questions like 'show me documents with validation errors'.
    """
    q = (
        select(ValidationResult, Document)
        .join(Document, Document.id == ValidationResult.document_id)
        .where(
            ValidationResult.tenant_id == tenant_id,
            ValidationResult.status == "fail",
        )
    )
    if document_id:
        q = q.where(ValidationResult.document_id == document_id)

    rows = db.execute(q.limit(20)).all()
    results = []
    for vr, doc in rows:
        text = (
            f"Validation {vr.severity} in {doc.original_filename}: "
            f"{vr.message}"
            + (f" (field: {vr.field_name})" if vr.field_name else "")
        )
        results.append(RetrievedChunk(
            chunk_id=vr.id,
            document_id=doc.id,
            original_filename=doc.original_filename,
            page_number=1,
            text=text,
            score=0.85,
            sql_matched=True,
        ))
    return results[:TOP_K]


def _sql_field_lookup(
    db: Session,
    query: str,
    tenant_id: uuid.UUID,
    document_id: Optional[uuid.UUID],
) -> list[RetrievedChunk]:
    """
    Look for extracted field values mentioned in the query.
    E.g. if the query contains "INV-2024001" we search extracted_fields for that value.

    This is a simple keyword match — it finds documents where any extracted field
    value appears verbatim (or closely) in the query text.
    """
    # Extract potential values from query (words longer than 3 chars)
    query_tokens = [t for t in query.split() if len(t) > 3]
    if not query_tokens:
        return []

    results: list[RetrievedChunk] = []

    q = (
        select(ExtractedField, Document)
        .join(Document, Document.id == ExtractedField.document_id)
        .where(
            ExtractedField.tenant_id == tenant_id,
            Document.tenant_id == tenant_id,
        )
    )
    if document_id:
        q = q.where(ExtractedField.document_id == document_id)

    rows = db.execute(q).all()

    for field, doc in rows:
        if not field.value:
            continue
        field_lower = field.value.lower()
        query_lower = query.lower()
        # Match if the field value or the query contains the other
        if field_lower in query_lower or any(t.lower() in field_lower for t in query_tokens):
            # Create a synthetic chunk from the field value (no bbox needed for SQL results)
            results.append(RetrievedChunk(
                chunk_id=field.id,
                document_id=doc.id,
                original_filename=doc.original_filename,
                page_number=field.evidence.get("page", 1) if field.evidence else 1,
                text=f"{field.field_name}: {field.value}",
                score=0.9,  # SQL matches get a high base score
                sql_matched=True,
                field_name=field.field_name,
                field_value=field.value,
            ))

    return results[:TOP_K]


def _vector_search(
    db: Session,
    query: str,
    tenant_id: uuid.UUID,
    document_id: Optional[uuid.UUID],
    limit: int,
) -> list[RetrievedChunk]:
    """
    Cosine similarity search using pgvector's <=> operator.
    Returns chunks ordered by similarity (highest first).
    """
    query_vec = embed_query(query)

    # Check if we have any non-zero embeddings (skip vector search if not)
    if all(v == 0.0 for v in query_vec):
        logger.debug("vector_search_skipped_zero_embedding")
        return []

    # Format vector as pgvector literal: '[0.1, 0.2, ...]'
    vec_literal = "[" + ",".join(f"{v:.6f}" for v in query_vec) + "]"

    # Build the similarity query with pgvector
    # cosine_distance = 1 - cosine_similarity, so we want 1 - (embedding <=> query)
    filters = "c.tenant_id = :tenant_id AND c.embedding IS NOT NULL"
    params: dict = {"tenant_id": str(tenant_id), "limit": limit}

    if document_id:
        filters += " AND c.document_id = :document_id"
        params["document_id"] = str(document_id)

    sql = text(f"""
        SELECT
            c.id AS chunk_id,
            c.document_id,
            c.page_number,
            c.text,
            1 - (c.embedding <=> '{vec_literal}'::vector) AS score,
            d.original_filename
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE {filters}
        ORDER BY c.embedding <=> '{vec_literal}'::vector
        LIMIT :limit
    """)

    try:
        rows = db.execute(sql, params).fetchall()
    except Exception as exc:
        # pgvector might not be enabled or chunks table might be empty
        logger.warning("vector_search_failed", error=str(exc))
        return []

    results = []
    for row in rows:
        if row.score < MIN_COSINE_SCORE:
            continue
        results.append(RetrievedChunk(
            chunk_id=row.chunk_id,
            document_id=row.document_id,
            original_filename=row.original_filename,
            page_number=row.page_number,
            text=row.text,
            score=float(row.score),
            sql_matched=False,
        ))
    return results


def _merge(
    sql_results: list[RetrievedChunk],
    vector_results: list[RetrievedChunk],
    top_k: int,
) -> list[RetrievedChunk]:
    """
    Merge SQL and vector results.
    - Deduplicate by (document_id, page_number, first 50 chars of text)
    - Boost score of chunks that appear in both sets
    - Sort by final score, return top_k
    """
    # Index vector results by a dedup key
    seen: dict[str, RetrievedChunk] = {}

    for chunk in vector_results:
        key = f"{chunk.document_id}:{chunk.page_number}:{chunk.text[:50]}"
        seen[key] = chunk

    # Boost chunks that also matched SQL
    sql_doc_ids = {str(c.document_id) for c in sql_results}
    for key, chunk in seen.items():
        if str(chunk.document_id) in sql_doc_ids:
            chunk.score = min(1.0, chunk.score + SQL_SCORE_BOOST)
            chunk.sql_matched = True

    # Add SQL-only results that weren't in vector results
    for chunk in sql_results:
        key = f"{chunk.document_id}:{chunk.page_number}:{chunk.text[:50]}"
        if key not in seen:
            seen[key] = chunk

    # Sort by score descending
    ranked = sorted(seen.values(), key=lambda c: c.score, reverse=True)
    return ranked[:top_k]
