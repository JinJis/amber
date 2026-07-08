"""Dependencies: trust the first-party web BFF (service token) + resolve the user."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import unquote

from fastapi import Depends, Header, HTTPException

from studioapi.config import settings
from studioapi.models import User
from studioapi.provision import ensure_user


async def require_service(x_service_token: Annotated[str | None, Header(alias="X-Service-Token")] = None) -> None:
    if not x_service_token or x_service_token != settings.service_token:
        raise HTTPException(401, "Invalid service token.")


async def current_user(
    x_user_email: Annotated[str | None, Header(alias="X-User-Email")] = None,
    x_user_name: Annotated[str | None, Header(alias="X-User-Name")] = None,
    x_user_image: Annotated[str | None, Header(alias="X-User-Image")] = None,
) -> User:
    if not x_user_email:
        raise HTTPException(401, "Missing authenticated user.")
    # name/image come from the OAuth session (via the web BFF), URI-encoded — seeded on first login.
    name = unquote(x_user_name) if x_user_name else None
    image = unquote(x_user_image) if x_user_image else None
    return await ensure_user(x_user_email, name=name, image=image)


ServiceDep = Depends(require_service)
UserDep = Depends(current_user)
