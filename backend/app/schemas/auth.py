"""
Pydantic schemas for auth endpoints.
Separating input/output schemas from ORM models lets us evolve them independently.
"""

import uuid
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator

from app.models.user import UserRole


class RegisterRequest(BaseModel):
    tenant_id: uuid.UUID
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    role: UserRole = UserRole.REVIEWER

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: Optional[str]
    role: UserRole
    is_active: bool

    model_config = {"from_attributes": True}
