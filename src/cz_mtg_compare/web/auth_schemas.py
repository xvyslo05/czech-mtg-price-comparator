"""Request / response schemas for the email-password auth endpoints.

Kept separate from web/schemas.py (which holds API request bodies for
the shop-side tools) so auth doesn't bleed into the public API surface
docs more than it has to.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

# Pragmatic floor — NIST 800-63B says 8 is the minimum. Production
# deployments should layer on a breach-list check (HIBP) before
# accepting; tracked as a follow-up.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 1024


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class AuthenticatedUser(BaseModel):
    user_id: str
    email: str
    email_verified: bool
