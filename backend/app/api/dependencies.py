"""FastAPI dependencies that authenticate the caller. Routes declare which one they need."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.services.auth import authenticate, require_active, require_admin
from app.auth import Profile

# auto_error is off so a missing token gets the same 401 body as an invalid one.
_bearer = HTTPBearer(auto_error=False, description="A Neon Auth JWT, sent as a Bearer token.")


def signed_in(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Profile:
    """Any signed-in caller, including a waitlisted one."""
    return authenticate(credentials.credentials if credentials else None)


def active_user(profile: Annotated[Profile, Depends(signed_in)]) -> Profile:
    """A caller who may use the app."""
    return require_active(profile)


def admin_user(profile: Annotated[Profile, Depends(signed_in)]) -> Profile:
    """An active admin. Anyone else gets 404."""
    return require_admin(profile)


SignedIn = Annotated[Profile, Depends(signed_in)]
ActiveUser = Annotated[Profile, Depends(active_user)]
AdminUser = Annotated[Profile, Depends(admin_user)]
