"""
Pydantic schemas for document endpoints.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.document import DocumentStatus, DocumentType


class DocumentOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    original_filename: str
    file_size_bytes: int
    mime_type: str
    page_count: Optional[int]
    status: DocumentStatus
    doc_type: DocumentType
    overall_confidence: Optional[float]
    grounding_rate: Optional[float]
    total_tokens: Optional[int]
    total_cost_usd: Optional[float]
    error_message: Optional[str]
    celery_task_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    total: int
    page: int
    page_size: int


class UploadResponse(BaseModel):
    document_id: uuid.UUID
    status: DocumentStatus
    task_id: str
    message: str
