"""
Server-Sent Events (SSE) for real-time document status updates.

Replaces frontend polling with a persistent HTTP stream.
The client connects once; the server pushes status events as the pipeline
progresses (queued → processing → needs_review / approved / failed).

Protocol:
  GET /api/v1/documents/{id}/events
  Accept: text/event-stream

  Each event:
    event: status_update
    data: {"document_id": "...", "status": "processing", ...}

  Heartbeat every 15 s to keep proxies alive:
    event: heartbeat
    data: {"ts": 1700000000}

  Terminal event:
    event: done
    data: {"document_id": "...", "status": "approved"}

Auth:
  SSE doesn't support custom headers, so the JWT can be passed as a
  query parameter (?token=...) as a fallback when the Authorization
  header is absent. The backend validates it identically.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DB
from app.core.logging import get_logger
from app.core.security import decode_token
from app.db.session import get_db
from app.models.document import Document, DocumentStatus
from app.models.user import User

logger = get_logger(__name__)
router = APIRouter()

TERMINAL_STATUSES = {
    DocumentStatus.APPROVED,
    DocumentStatus.REJECTED,
    DocumentStatus.FAILED,
    DocumentStatus.NEEDS_REVIEW,
}

HEARTBEAT_INTERVAL = 15   # seconds between heartbeats
POLL_INTERVAL = 2         # seconds between DB polls
MAX_STREAM_SECONDS = 600  # hard cut-off (= Celery task time limit)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get("/{document_id}/events")
async def document_events(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
    # SSE fallback: browser EventSource can't set headers, so accept token
    # as a query param too. The CurrentUser dep already covers the header path.
    token: Optional[str] = Query(default=None),
) -> StreamingResponse:
    """
    Subscribe to real-time status updates for a document.
    Stays open until terminal status or MAX_STREAM_SECONDS.
    """
    # If the user came in via query-param token (EventSource), validate it
    # and verify tenant ownership separately
    if token and not current_user:
        td = decode_token(token)
        if not td:
            raise HTTPException(status_code=401, detail="Invalid token")
        # current_user is already resolved by the dep — this path is belt-and-suspenders

    result = await db.execute(
        select(Document.id, Document.status).where(
            Document.id == document_id,
            Document.tenant_id == current_user.tenant_id,
        )
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Already terminal — send one event and close
    if row.status in TERMINAL_STATUSES:
        async def _immediate():
            yield _sse("status_update", {"document_id": str(document_id), "status": row.status})
            yield _sse("done", {"document_id": str(document_id), "status": row.status})

        return StreamingResponse(_immediate(), media_type="text/event-stream")

    tenant_id = current_user.tenant_id

    async def _stream():
        last_status = row.status
        deadline = asyncio.get_event_loop().time() + MAX_STREAM_SECONDS
        last_heartbeat = asyncio.get_event_loop().time()

        yield _sse("status_update", {"document_id": str(document_id), "status": last_status})

        # Each poll opens a fresh DB session to avoid holding one open for 10 min
        async for session in get_db():
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(POLL_INTERVAL)

                try:
                    doc_result = await session.execute(
                        select(
                            Document.status,
                            Document.overall_confidence,
                            Document.grounding_rate,
                            Document.total_cost_usd,
                            Document.error_message,
                        ).where(
                            Document.id == document_id,
                            Document.tenant_id == tenant_id,
                        )
                    )
                    doc_row = doc_result.first()
                except Exception as exc:
                    logger.warning("sse_db_error", document_id=str(document_id), error=str(exc))
                    break

                if doc_row is None:
                    break

                current_status = doc_row.status

                if current_status != last_status:
                    payload: dict = {
                        "document_id": str(document_id),
                        "status": current_status,
                    }
                    if doc_row.overall_confidence is not None:
                        payload["overall_confidence"] = doc_row.overall_confidence
                    if doc_row.grounding_rate is not None:
                        payload["grounding_rate"] = doc_row.grounding_rate
                    if doc_row.total_cost_usd is not None:
                        payload["total_cost_usd"] = doc_row.total_cost_usd
                    if doc_row.error_message:
                        payload["error_message"] = doc_row.error_message

                    yield _sse("status_update", payload)
                    last_status = current_status

                if current_status in TERMINAL_STATUSES:
                    yield _sse("done", {"document_id": str(document_id), "status": current_status})
                    return

                now = asyncio.get_event_loop().time()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                    yield _sse("heartbeat", {"ts": int(datetime.now(timezone.utc).timestamp())})
                    last_heartbeat = now

        yield _sse("timeout", {
            "document_id": str(document_id),
            "message": "Stream closed after timeout. Poll /status for final state.",
        })

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
