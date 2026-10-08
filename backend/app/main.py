"""
FastAPI application entry point.

Wires together:
- Startup/shutdown lifecycle (DB pool, logging)
- CORS middleware
- All API routers
- Global exception handlers (so unhandled errors return structured JSON, not tracebacks)
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.api.routes import auth, documents, health

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    configure_logging()
    logger.info("cargo_copilot_starting", environment=settings.environment)
    yield
    logger.info("cargo_copilot_stopping")


app = FastAPI(
    title="Cargo Copilot API",
    description="Agentic document processing for logistics and freight",
    version="0.1.0",
    lifespan=lifespan,
    # Don't expose docs in production (security hygiene)
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url="/redoc" if settings.environment != "production" else None,
)

# ── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Global exception handler ──────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all handler so the API always returns structured JSON.
    We log the error with full context but return a generic message to the client
    (never expose internal error details in production).
    """
    logger.exception(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        error=str(exc),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again."},
    )


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(health.router, tags=["health"])
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])
