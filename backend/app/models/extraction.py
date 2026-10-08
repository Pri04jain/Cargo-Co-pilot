import uuid
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import String, Integer, Float, ForeignKey, Enum, Text, JSON, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, TenantMixin


class FieldStatus(str, PyEnum):
    GROUNDED = "grounded"       # matched to a bounding box in the document
    UNGROUNDED = "ungrounded"   # LLM returned a value but we couldn't find it on the page
    CORRECTED = "corrected"     # a human reviewer changed the value


class Extraction(Base, TimestampMixin, TenantMixin):
    """
    One extraction run for a document.
    A document can have multiple extractions (initial + retries).
    prompt_version and model are stored so we can reproduce and compare runs.
    """
    __tablename__ = "extractions"

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

    # Which prompt file and model version produced this extraction
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)

    # Attempt number: 0 = initial, 1 = first retry, 2 = second retry
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Full raw LLM response (for debugging)
    raw_response: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Whether this is the current/active extraction for this document
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    document: Mapped["Document"] = relationship("Document", back_populates="extractions")  # type: ignore[name-defined]
    fields: Mapped[list["ExtractedField"]] = relationship(
        "ExtractedField", back_populates="extraction", cascade="all, delete-orphan"
    )


class ExtractedField(Base, TimestampMixin, TenantMixin):
    """
    One extracted field from one extraction run.

    The evidence column stores the grounding info:
    {page: int, bbox: {x0,y0,x1,y1}, snippet: str}

    This is the core data structure that drives evidence highlighting in the UI.
    Every field must carry this — if we can't ground it, status=ungrounded.
    """
    __tablename__ = "extracted_fields"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extractions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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

    field_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Evidence: {page: int, bbox: {x0,y0,x1,y1}, snippet: str}
    evidence: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    status: Mapped[FieldStatus] = mapped_column(
        Enum(FieldStatus, name="field_status"),
        default=FieldStatus.UNGROUNDED,
        nullable=False,
    )

    extraction: Mapped["Extraction"] = relationship("Extraction", back_populates="fields")
