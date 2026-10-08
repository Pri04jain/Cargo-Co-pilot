import uuid
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import String, ForeignKey, Enum, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, TenantMixin


class ReviewActionType(str, PyEnum):
    CORRECTION = "correction"   # reviewer changed a field value
    APPROVE = "approve"         # reviewer approved the document
    REJECT = "reject"           # reviewer rejected the document
    COMMENT = "comment"         # reviewer left a comment without changing data


class ReviewAction(Base, TimestampMixin, TenantMixin):
    """
    Every action a reviewer takes is recorded here.
    Corrections (original vs new value) feed the evaluation set.
    This gives us real-world ground truth from production usage.
    """
    __tablename__ = "review_actions"

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
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    action_type: Mapped[ReviewActionType] = mapped_column(
        Enum(ReviewActionType, name="review_action_type"), nullable=False
    )
    field_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    original_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    corrected_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    document: Mapped["Document"] = relationship("Document", back_populates="review_actions")  # type: ignore[name-defined]
