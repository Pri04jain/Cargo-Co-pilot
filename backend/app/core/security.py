"""
JWT authentication utilities.

Design choices:
- Short-lived access tokens (60 min) + long-lived refresh tokens (30 days).
- Refresh tokens are opaque random bytes stored as bcrypt hashes in the DB,
  so a DB dump can't be used to forge new sessions.
- Every access token encodes user_id AND tenant_id so route handlers have
  both without an extra DB lookup.
- Refresh token rotation: each /auth/refresh call revokes the old token and
  issues a new one (limits the window if a token is stolen).
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Refresh token TTL — configurable via settings, defaults to 30 days
REFRESH_TOKEN_EXPIRE_DAYS = 30


class TokenData(BaseModel):
    user_id: UUID
    tenant_id: UUID
    email: str


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_token(raw: str) -> str:
    """Hash an opaque token for storage (same bcrypt as passwords)."""
    return pwd_context.hash(raw)


def verify_token_hash(raw: str, hashed: str) -> bool:
    return pwd_context.verify(raw, hashed)


def generate_refresh_token() -> str:
    """Return a cryptographically random 64-byte hex string."""
    return secrets.token_hex(64)


def create_access_token(user_id: UUID, tenant_id: UUID, email: str) -> str:
    """Create a signed JWT that expires after ACCESS_TOKEN_EXPIRE_MINUTES."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "email": email,
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def refresh_token_expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)


def decode_token(token: str) -> Optional[TokenData]:
    """Decode and validate a JWT. Returns None if invalid/expired."""
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
        return TokenData(
            user_id=UUID(payload["sub"]),
            tenant_id=UUID(payload["tenant_id"]),
            email=payload["email"],
        )
    except (JWTError, KeyError, ValueError):
        return None
