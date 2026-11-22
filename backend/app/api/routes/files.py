"""
File serving — returns the original uploaded file so the frontend PDF viewer
can render it.

Security:
  - Scoped to the current tenant (storage path is verified via the DB record)
  - No path traversal: we look up the stored path in the DB, never construct
    it from user input
  - Only serves files in the configured UPLOAD_DIR
"""

import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, DB
from app.core.config import settings
from app.models.document import Document

router = APIRouter()


@router.get("/{document_id}/file")
async def get_document_file(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
) -> FileResponse:
    """Serve the original document file (PDF or image)."""
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == current_user.tenant_id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    storage_path = Path(doc.storage_path)

    # Verify the file is within the expected upload directory (security guard)
    try:
        storage_path.resolve().relative_to(Path(settings.upload_dir).resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not storage_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    media_type = doc.mime_type or "application/octet-stream"
    return FileResponse(
        path=str(storage_path),
        media_type=media_type,
        filename=doc.original_filename,
        headers={
            # Allow the PDF viewer iframe/canvas to load the file
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "private, max-age=3600",
        },
    )
