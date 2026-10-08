import uuid
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import String, Integer, Float, ForeignKey, Enum, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, TenantMixin


class DocumentStatus(str, PyEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class DocumentType(str, PyEnum):
    BILL_OF_LADING = "bill_of_lading"
    COMMERCIAL_INVOICE = "commercial_invoice"
    PACKING_LIST = "packing_list"
    UNKNOWN = "unknown"


class Document(Base, TimestampMixin, TenantMixin):
    """
    Master record for every uploaded file.
    The pipeline updates status as it progresses through nodes.
    cost_usd and token counts are filled in at the finalize node.
    """
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # File metadata
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Pipeline state
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"),
        default=DocumentStatus.QUEUED,
        nullable=False,
        index=True,
    )
    doc_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, name="document_type"),
        default=DocumentType.UNKNOWN,
        nullable=False,
    )

    # Retry tracking (max 2 retries per spec)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Aggregate scores (filled after extraction)
    overall_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    grounding_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Cost tracking (filled at finalize)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Error details when status=failed
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Celery task ID for status polling
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Relationships
    pages: Mapped[list["DocumentPage"]] = relationship(
        "DocumentPage", back_populates="document", cascade="all, delete-orphan"
    )
    extractions: Mapped[list["Extraction"]] = relationship(  # type: ignore[name-defined]
        "Extraction", back_populates="document", cascade="all, delete-orphan"
    )
    validation_results: Mapped[list["ValidationResult"]] = relationship(  # type: ignore[name-defined]
        "ValidationResult", back_populates="document", cascade="all, delete-orphan"
    )
    routing_decisions: Mapped[list["RoutingDecision"]] = relationship(  # type: ignore[name-defined]
        "RoutingDecision", back_populates="document", cascade="all, delete-orphan"
    )
    review_actions: Mapped[list["ReviewAction"]] = relationship(  # type: ignore[name-defined]
        "ReviewAction", back_populates="document", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(  # type: ignore[name-defined]
        "AuditLog", back_populates="document", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["Chunk"]] = relationship(  # type: ignore[name-defined]
        "Chunk", back_populates="document", cascade="all, delete-orphan"
    )


class DocumentPage(Base, TimestampMixin, TenantMixin):
    """
    Parsed content for one page: raw text plus word-level bounding boxes.
    The words JSON is a list of {text, bbox: {x0,y0,x1,y1}, page} objects.
    Stored as JSONB so we can query it if needed, but primarily read by the pipeline.
    """
    __tablename__ = "document_pages"

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

    # Full page text (for RAG chunking and display)
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Structured word boxes: [{text, bbox: {x0,y0,x1,y1}}]
    words: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Page image dimensions (needed to scale bbox coordinates for the UI)
    width: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    height: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    document: Mapped["Document"] = relationship("Document", back_populates="pages")
