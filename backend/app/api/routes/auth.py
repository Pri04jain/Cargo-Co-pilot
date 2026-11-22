"""
Auth routes: register, login, refresh, logout, /me.

Token strategy:
- Login returns a short-lived access token (60 min) + a long-lived refresh
  token (30 days). The refresh token is an opaque random value stored as a
  bcrypt hash in the DB — never in plain text.
- POST /auth/refresh validates the refresh token, revokes it, and issues a
  fresh pair (rotation). This limits the damage if a refresh token is stolen.
- POST /auth/logout revokes the refresh token so it can't be rotated again.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DB, CurrentUser
from app.core.config import settings
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    refresh_token_expires_at,
    verify_password,
    verify_token_hash,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)

router = APIRouter()


def _build_token_response(user: User, user_agent: str | None) -> tuple[TokenResponse, RefreshToken]:
    """Create a new access + refresh token pair. Returns both the schema and the ORM object."""
    access_token = create_access_token(
        user_id=user.id, tenant_id=user.tenant_id, email=user.email
    )
    raw_refresh = generate_refresh_token()
    refresh_orm = RefreshToken(
        user_id=user.id,
        tenant_id=user.tenant_id,
        token_hash=hash_token(raw_refresh),
        expires_at=refresh_token_expires_at(),
        user_agent=user_agent,
    )
    response = TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )
    return response, refresh_orm


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, request: Request, db: DB) -> User:
    """
    Register a new user under an existing tenant.
    In production you'd send a verification email; for the demo we activate immediately.
    """
    from app.core.rate_limit import rate_limit
    await rate_limit(request, "register")

    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        tenant_id=payload.tenant_id,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request, db: DB) -> TokenResponse:
    from app.core.rate_limit import rate_limit
    await rate_limit(request, "login")

    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    user_agent = request.headers.get("user-agent")
    token_response, refresh_orm = _build_token_response(user, user_agent)
    db.add(refresh_orm)
    await db.flush()
    return token_response


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, request: Request, db: DB) -> TokenResponse:
    """
    Validate a refresh token, revoke it, and issue a fresh pair.
    Returns 401 if the token is expired, revoked, or not found.
    """
    from app.core.rate_limit import rate_limit
    await rate_limit(request, "refresh")

    # Scan active tokens for this hash. We load recent non-revoked tokens;
    # in production add a partial index on expires_at to keep this fast.
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.revoked == False,  # noqa: E712
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
    )
    # bcrypt compare is O(n candidates) — keep issued refresh tokens bounded
    # per-user. For a portfolio project the total count stays small.
    token_record: RefreshToken | None = None
    for candidate in result.scalars().all():
        if verify_token_hash(payload.refresh_token, candidate.token_hash):
            token_record = candidate
            break

    if token_record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Load the associated user
    user_result = await db.execute(
        select(User).where(User.id == token_record.user_id, User.is_active == True)  # noqa: E712
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # Rotate: revoke old, issue new
    token_record.revoked = True
    user_agent = request.headers.get("user-agent")
    token_response, new_refresh_orm = _build_token_response(user, user_agent)
    db.add(new_refresh_orm)
    await db.flush()
    return token_response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: LogoutRequest, current_user: CurrentUser, db: DB) -> None:
    """
    Revoke a refresh token. The access token naturally expires; clients should
    discard it locally. Returns 204 whether or not the token was found.
    """
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == current_user.id,
            RefreshToken.revoked == False,  # noqa: E712
        )
    )
    for candidate in result.scalars().all():
        if verify_token_hash(payload.refresh_token, candidate.token_hash):
            candidate.revoked = True
            await db.flush()
            break
    # Always 204 — don't reveal whether the token existed


@router.get("/me", response_model=UserOut)
async def me(current_user: CurrentUser) -> User:
    """Returns the currently authenticated user's profile."""
    return current_user
