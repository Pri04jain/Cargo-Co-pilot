"""
JWT authentication utilities.

Design choices:
- Access tokens only (no refresh tokens) — simpler for a portfolio project;
  a production app would add refresh + rotation.
- Passwords are bcrypt-hashed via passlib. Never stored in plain text.
- Every token encodes sub (user_id) AND tenant_id so we can scope DB queries
  without an extra lookup on every request.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class TokenData(BaseModel):
    user_id: UUID
    tenant_id: UUID
    email: str


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


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
