"""
Smart Data Gateway — Party JWT Authentication.

Parties (borrowers, lenders, lessees, lessors) authenticate via
POST /api/party/auth and receive a signed JWT.  All party-portal
endpoints use this dependency to verify the token and extract the
caller's party identity.

Auth flow (POC):
  1.  Party submits entity_id + party_type to /api/party/auth
  2.  Gateway verifies entity_id exists in contracts.parties with that role
  3.  Issues a signed HS256 JWT (1-hour validity)
  4.  Party includes token in every subsequent request:
        Authorization: Bearer <token>

In production this would integrate with an SSO / OAuth provider so
parties authenticate with their existing bank or dealer credentials.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request
from jose import JWTError, jwt

from shared.config import get_settings


@dataclass
class PartyContext:
    """Authenticated party identity extracted from a verified party JWT."""

    entity_id: str
    party_type: str  # borrower | lessee | lender | lessor | dealer | …
    name: str


async def get_party_context(request: Request) -> PartyContext:
    """FastAPI dependency: validate party Bearer JWT → PartyContext.

    Raises HTTP 401 if the header is absent, the token is invalid,
    or the token has expired.
    """
    settings = get_settings()

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail=(
                "Party portal requires authentication. "
                "Obtain a token from POST /api/party/auth."
            ),
        )

    token = auth_header[len("Bearer "):]
    try:
        payload = jwt.decode(
            token,
            settings.dashboard_jwt_secret,
            algorithms=["HS256"],
            options={"require": ["sub", "party_type", "exp"]},
        )
    except JWTError:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired party token.",
        )

    return PartyContext(
        entity_id=payload["sub"],
        party_type=payload["party_type"],
        name=payload.get("name", payload["sub"]),
    )
