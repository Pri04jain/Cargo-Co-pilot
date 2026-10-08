import uuid
from typing import Optional

from sqlalchemy import String, ForeignKey, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, TenantMixin


class AuditLog(Base, TimestampMixin, TenantMixin):
    """
    Append-only audit trail. We never UPDATE or DELETE rows here.
    Records every state change, LLM call, routing decision, and human action.

    Why append-only? Makes it tamper-evident and suitable for compliance.
    In a real system you'd add a hash chain; for now uniqueness + ordering is enough.
    """
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Actor can be a user email or "system" for automated pipeline steps
    actor: Mapped[str] = mapped_column(String(255), nullable=False, default="system")

    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # e.g. document_uploaded, status_changed, llm_call, rule_fired,
    #      field_corrected, document_approved, document_rejected

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Structured data for this event (status before/after, rule name, etc.)
    metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    document: Mapped[Optional["Document"]] = relationship("Document", back_populates="audit_logs")  # type: ignore[name-defined]
