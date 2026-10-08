import uuid
from typing import Optional

from sqlalchemy import String, Integer, Float, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, TenantMixin


class LLMCall(Base, TimestampMixin, TenantMixin):
    """
    Record of every LLM API call: tokens, cost, latency.
    Used for cost tracking in the UI and for the eval harness metrics.

    Storing this separately from extractions lets us answer questions like
    "what did this document cost?" and "what's the p95 latency per model?".
    """
    __tablename__ = "llm_calls"

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

    # Pipeline node that made this call (classify, extract, retry_extract, etc.)
    pipeline_node: Mapped[str] = mapped_column(String(100), nullable=False)

    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Langfuse trace ID for correlation
    trace_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
