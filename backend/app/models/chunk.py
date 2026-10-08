import uuid
from typing import Optional

from sqlalchemy import String, Integer, ForeignKey, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.models.base import Base, TimestampMixin, TenantMixin

# Embedding dimension for text-embedding-3-small (OpenAI) or equivalent
EMBEDDING_DIM = 1536


class Chunk(Base, TimestampMixin, TenantMixin):
    """
    A text chunk from a document page, with its embedding vector for RAG.
    We chunk per page (or smaller) so retrieved context is locatable.

    Why store page_number and field_context?
    - page_number: lets Ask Copilot cite the exact page.
    - field_context: lets us attach extracted field values alongside the chunk
      so hybrid SQL+vector search can combine both.
    """
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)  # order within document
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # pgvector column — cosine similarity search happens on this
    embedding: Mapped[Optional[list]] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    # Snapshot of key extracted fields for hybrid retrieval
    field_context: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    document: Mapped["Document"] = relationship("Document", back_populates="chunks")  # type: ignore[name-defined]
