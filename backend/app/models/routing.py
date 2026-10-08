import uuid
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import String, ForeignKey, Enum, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, TenantMixin


class RoutingDecisionType(str, PyEnum):
    AUTO_APPROVE = "auto_approve"
    MANAGER_APPROVAL = "manager_approval"
    EXCEPTION_QUEUE = "exception_queue"


class RoutingDecision(Base, TimestampMixin, TenantMixin):
    """
    The decision produced by the YAML rules engine.
    Stores which rule fired and the full evaluation context for auditability.
    """
    __tablename__ = "routing_decisions"

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

    decision: Mapped[RoutingDecisionType] = mapped_column(
        Enum(RoutingDecisionType, name="routing_decision_type"), nullable=False
    )
    rule_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Full snapshot of rule evaluation (which rules were tested, which fired)
    evaluation_context: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Which rules file version was used
    rules_version: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    document: Mapped["Document"] = relationship("Document", back_populates="routing_decisions")  # type: ignore[name-defined]
