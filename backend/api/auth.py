"""Owner login API, and the check every other route runs first.

The browser never talks to Supabase: it sends the owner's email and password here, the backend
asks Supabase Auth (which keeps the passwords hashed) and hands back a short-lived access token
plus a refresh token. Every other request must carry the access token
(``Authorization: Bearer <token>``); ``require_owner`` checks it and looks up the business the
login belongs to (business_users), so each route only ever reads that business's rows.
There is no sign-up endpoint: the team creates each shop's owner login in Supabase.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from database.client import AuthError, LoginSession
from services.invoice_service import WorkflowError

if TYPE_CHECKING:
    from main import AppServices

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Email and password typed on the login page."""

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    """The refresh token from the last login or refresh."""

    refresh_token: str = Field(min_length=1)


class LoginReply(BaseModel):
    """A logged-in session: tokens for the browser and the shop it opens."""

    access_token: str
    refresh_token: str
    expires_in: int
    email: str | None
    business_id: str
    business_name: str


def _bearer(request: Request) -> str | None:
    """The access token from the Authorization header, if any."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def _auth(services: AppServices):
    """The Supabase Auth client; refuses (503) when login is not configured."""
    if services.auth is None:
        raise WorkflowError(503, "login_not_configured", "Login is not configured on the server.")
    return services.auth


async def _reply(services: AppServices, session: LoginSession) -> LoginReply:
    """A login reply, refused (403) when the login is not linked to any shop."""
    business = await services.db.business_for_user(session.user_id)
    if business is None:
        await services.auth.sign_out(session.access_token)
        raise WorkflowError(403, "no_business", "This login is not linked to a shop. Ask the BusinessSense team.")
    return LoginReply(access_token=session.access_token, refresh_token=session.refresh_token,
                      expires_in=session.expires_in, email=session.email,
                      business_id=business[0], business_name=business[1])


async def require_owner(request: Request) -> None:
    """Run before every data route: a valid login token, linked to a shop, or 401/403.

    Stores the shop's business id on the request for ``AppServices.business_id_for``. With
    AUTH_REQUIRED=false (local tests only) the configured DEFAULT_BUSINESS_ID is used instead.
    """
    services: AppServices = request.app.state.services
    if not services.settings.auth_required:
        request.state.business_id = services.settings.default_business_id
        return
    token = _bearer(request)
    if token is None:
        raise WorkflowError(401, "not_logged_in", "Log in to see this.")
    user_id = await _auth(services).user_id(token)
    if user_id is None:
        raise WorkflowError(401, "session_expired", "Your login has expired. Log in again.")
    business = await services.db.business_for_user(user_id)
    if business is None:
        raise WorkflowError(403, "no_business", "This login is not linked to a shop. Ask the BusinessSense team.")
    request.state.user_id = user_id
    request.state.business_id, request.state.business_name = business


@router.post("/login", response_model=LoginReply)
async def login(request: Request, body: LoginRequest) -> LoginReply:
    """Check the owner's email and password with Supabase Auth and open their shop."""
    services: AppServices = request.app.state.services
    try:
        session = await _auth(services).sign_in(body.email.strip(), body.password)
    except AuthError as exc:
        raise WorkflowError(exc.status_code, "login_failed", exc.message) from exc
    return await _reply(services, session)


@router.post("/refresh", response_model=LoginReply)
async def refresh(request: Request, body: RefreshRequest) -> LoginReply:
    """Renew a session before its access token expires (about an hour)."""
    services: AppServices = request.app.state.services
    try:
        session = await _auth(services).refresh(body.refresh_token)
    except AuthError as exc:
        raise WorkflowError(exc.status_code, "session_expired", exc.message) from exc
    return await _reply(services, session)


@router.post("/logout")
async def logout(request: Request) -> dict:
    """End the session: Supabase revokes it, so its refresh token cannot be used again."""
    services: AppServices = request.app.state.services
    token = _bearer(request)
    if token and services.auth is not None:
        await services.auth.sign_out(token)
    return {"ok": True}
