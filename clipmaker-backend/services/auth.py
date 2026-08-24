from __future__ import annotations

from functools import lru_cache

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from config import AUTH_REQUIRED, SUPABASE_CONFIGURED, SUPABASE_SERVER_KEY, SUPABASE_URL


class AuthenticatedUser(BaseModel):
    id: str
    email: str | None = None


bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _auth_client():
    from supabase import create_client

    return create_client(SUPABASE_URL, SUPABASE_SERVER_KEY)


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer),
) -> AuthenticatedUser | None:
    if credentials is None:
        if AUTH_REQUIRED:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required")
        return None
    if not SUPABASE_CONFIGURED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase authentication is not configured",
        )
    try:
        response = _auth_client().auth.get_user(credentials.credentials)
        user = response.user
        if not user:
            raise ValueError("No user returned")
        return AuthenticatedUser(id=str(user.id), email=getattr(user, "email", None))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token") from exc
