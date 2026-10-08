import uuid
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import String, ForeignKey, Enum, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, TenantMixin


class ValidationSeverity(str, PyEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ValidationStatus(str, PyEnum):
    PASS = "pass"
    FAIL = "fail"


class ValidationResult(Base, TimestampMixin, TenantMixin):
    """
    One validation check result. Checks are deterministic (no LLM).
    Examples: required field missing, invoice total mismatch, ISO 6346 format error.
    """
    __tablename__ = "validation_results"

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

    check_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[ValidationStatus] = mapped_column(
        Enum(ValidationStatus, name="validation_status"), nullable=False
    )
    severity: Mapped[ValidationSeverity] = mapped_column(
        Enum(ValidationSeverity, name="validation_severity"), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    document: Mapped["Document"] = relationship("Document", back_populates="validation_results")  # type: ignore[name-defined]
