"""
Ask Copilot API — conversational Q&A over processed documents.

POST /api/v1/chat
  - question: natural language question
  - document_id: optional — scope to a single document

The endpoint uses a sync DB session for the retrieval query
(pgvector search is synchronous), then returns the structured response.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DB
from app.core.logging import get_logger
from app.db.session import sync_engine
from app.rag.chat_service import ChatResponse, ask

logger = get_logger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    question: str
    document_id: Optional[str] = None


class CitationOut(BaseModel):
    document_id: str
    page_number: int
    filename: str
    snippet: str


class ChatResponseOut(BaseModel):
    answer: str
    citations: list[CitationOut]
    sufficient_context: bool
    retrieved_chunks: int
    tokens_used: int
    cost_usd: float


@router.post("", response_model=ChatResponseOut)
async def chat(
    payload: ChatRequest,
    current_user: CurrentUser,
) -> ChatResponseOut:
    """
    Answer a question about processed documents using hybrid RAG.

    Uses a sync DB session because pgvector's <=> operator runs in a
    synchronous query. The async session is used for all other routes.
    """
    if not payload.question or len(payload.question.strip()) < 3:
        raise HTTPException(status_code=422, detail="Question is too short")

    if len(payload.question) > 2000:
        raise HTTPException(status_code=422, detail="Question is too long (max 2000 chars)")

    doc_id: Optional[uuid.UUID] = None
    if payload.document_id:
        try:
            doc_id = uuid.UUID(payload.document_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid document_id format")

    # Use sync session for the retrieval query
    with Session(sync_engine) as db:
        response = ask(
            db=db,
            question=payload.question.strip(),
            tenant_id=current_user.tenant_id,
            document_id=doc_id,
        )

    logger.info(
        "chat_answered",
        question_len=len(payload.question),
        sufficient=response.sufficient_context,
        citations=len(response.citations),
        tokens=response.tokens_used,
    )

    return ChatResponseOut(
        answer=response.answer,
        citations=[
            CitationOut(
                document_id=c.document_id,
                page_number=c.page_number,
                filename=c.filename,
                snippet=c.snippet,
            )
            for c in response.citations
        ],
        sufficient_context=response.sufficient_context,
        retrieved_chunks=response.retrieved_chunks,
        tokens_used=response.tokens_used,
        cost_usd=response.cost_usd,
    )
