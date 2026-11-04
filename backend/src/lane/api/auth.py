"""Demo sign-in with roles. Production replaces this with SSO (docs/SECURITY.md).

A token is a signed, expiring statement of a role. Requests without a token get the configured
demo role so the prototype opens without a sign-in screen; a bad or expired token is refused.
"""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any, Literal, cast

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel

from lane.api.deps import SettingsDep
from lane.config import Settings

Role = Literal["underwriter", "senior_underwriter", "viewer", "ops", "admin"]
ROLES: dict[str, str] = {
    "underwriter": "Underwriter",
    "senior_underwriter": "Senior underwriter",
    "viewer": "Viewer",
    "ops": "Operations",
    "admin": "Administrator",
}
# The demo's people, one per role, so the audit names who did what. Invented names; production
# takes the person and their role from SSO (docs/SECURITY.md).
PEOPLE: dict[str, tuple[str, str]] = {
    "priya": ("Priya Shah", "underwriter"),
    "daniel": ("Daniel Reyes", "senior_underwriter"),
    "guest": ("Guest reviewer", "viewer"),
    "sam": ("Sam Patel", "ops"),
    "alex": ("Alex Kim", "admin"),
}
# What each role may do beyond reading submission metadata.
PERMISSIONS: dict[str, frozenset[str]] = {
    "underwriter": frozenset(
        {"view_content", "reveal_pii", "review", "process", "approve_spend", "delete"}
    ),
    # A senior underwriter may do all an underwriter may; decision authority is in config
    "senior_underwriter": frozenset(
        {"view_content", "reveal_pii", "review", "process", "approve_spend", "delete"}
    ),
    "viewer": frozenset({"view_content"}),  # read-only; the default without a sign-in
    "ops": frozenset({"process", "ops"}),
    "admin": frozenset({"view_content", "ops", "evals"}),
}

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@dataclass(frozen=True)
class Viewer:
    role: str
    signed_in: bool
    name: str | None = None  # the person signed in, when the sign-in names one

    @property
    def actor(self) -> str:
        return f"user:{self.name or self.role}"

    def can(self, permission: str) -> bool:
        return permission in PERMISSIONS.get(self.role, frozenset())

    def may_decide(self, action: str, settings: Settings) -> bool:
        """Decision authority by role (settings.decision_authority): what this person may decide."""
        return action in settings.decision_authority.get(self.role, [])


def _sign(payload: bytes, settings: Settings) -> str:
    key = settings.session_secret.get_secret_value().encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def issue_token(
    role: str, settings: Settings, *, now: float | None = None, name: str | None = None
) -> str:
    claims: dict[str, object] = {
        "role": role,
        "exp": int((now or time.time()) + settings.session_ttl_hours * 3600),
    }
    if name:
        claims["name"] = name
    body = base64.urlsafe_b64encode(json.dumps(claims, sort_keys=True).encode()).decode()
    return f"{body}.{_sign(body.encode(), settings)}"


def read_token(token: str, settings: Settings, *, now: float | None = None) -> str | None:
    claims = read_claims(token, settings, now=now)
    return claims[0] if claims else None


def read_claims(
    token: str, settings: Settings, *, now: float | None = None
) -> tuple[str, str | None] | None:
    """The role and the person's name a valid token carries; None when it is not valid."""
    body, _, signature = token.partition(".")
    if not body or not hmac.compare_digest(signature, _sign(body.encode(), settings)):
        return None
    try:
        loaded: object = json.loads(base64.urlsafe_b64decode(body.encode()))
    except ValueError:
        return None
    if not isinstance(loaded, dict):
        return None
    claims = cast(dict[str, object], loaded)
    role, exp = claims.get("role"), claims.get("exp")
    if role not in ROLES or not isinstance(exp, int) or exp < (now or time.time()):
        return None
    name = claims.get("name")
    return str(role), name if isinstance(name, str) else None


def get_viewer(
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
    access_token: Annotated[str | None, Query(include_in_schema=False)] = None,
) -> Viewer:
    """The bearer header, or `access_token` for what cannot send headers (img, EventSource)."""
    if authorization:
        scheme, _, token = authorization.partition(" ")
        token = token.strip() if scheme.lower() == "bearer" else ""
    elif access_token:
        token = access_token
    else:
        return Viewer(settings.demo_default_role, signed_in=False)
    claims = read_claims(token, settings) if token else None
    if claims is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign-in expired or invalid")
    return Viewer(claims[0], signed_in=True, name=claims[1])


ViewerDep = Annotated[Viewer, Depends(get_viewer)]


def _requires(permission: str) -> Callable[[Viewer], Viewer]:
    def check(viewer: ViewerDep) -> Viewer:
        if not viewer.can(permission):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"The {ROLES.get(viewer.role, viewer.role).lower()} role cannot do this",
            )
        return viewer

    return check


# One dependency per permission, used as a parameter type: `viewer: Reviewer`.
ContentViewer = Annotated[Viewer, Depends(_requires("view_content"))]
Reviewer = Annotated[Viewer, Depends(_requires("review"))]
Revealer = Annotated[Viewer, Depends(_requires("reveal_pii"))]
Processor = Annotated[Viewer, Depends(_requires("process"))]
SpendApprover = Annotated[Viewer, Depends(_requires("approve_spend"))]
Deleter = Annotated[Viewer, Depends(_requires("delete"))]
OpsViewer = Annotated[Viewer, Depends(_requires("ops"))]


class LoginIn(BaseModel):
    role: Role
    person: str | None = None  # one of PEOPLE; their role is used


class PersonOut(BaseModel):
    id: str
    name: str
    role: str
    role_label: str


class SessionOut(BaseModel):
    role: str
    role_label: str
    name: str | None
    signed_in: bool
    permissions: list[str]
    authority: list[str]  # the decisions this role may record (settings.decision_authority)
    token: str | None = None


def _session(viewer: Viewer, settings: Settings, token: str | None = None) -> SessionOut:
    return SessionOut(
        role=viewer.role,
        role_label=ROLES.get(viewer.role, viewer.role),
        name=viewer.name,
        signed_in=viewer.signed_in,
        permissions=sorted(PERMISSIONS.get(viewer.role, frozenset())),
        authority=sorted(settings.decision_authority.get(viewer.role, [])),
        token=token,
    )


def _demo_only(settings: Settings) -> None:
    if not settings.demo_login:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Demo sign-in is off; sign in with Cognito")


@router.get("/people", operation_id="list_demo_people")
def people(settings: SettingsDep) -> list[PersonOut]:
    """Who can sign in to the demo, one per role."""
    _demo_only(settings)
    return [
        PersonOut(id=pid, name=name, role=role, role_label=ROLES[role])
        for pid, (name, role) in PEOPLE.items()
    ]


@router.post("/demo-login", operation_id="demo_login")
def demo_login(body: LoginIn, settings: SettingsDep) -> SessionOut:
    _demo_only(settings)
    if body.person is not None:
        if body.person not in PEOPLE:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown person")
        name, role = PEOPLE[body.person]
    else:
        name, role = None, body.role
    viewer = Viewer(role, signed_in=True, name=name)
    return _session(viewer, settings, issue_token(role, settings, name=name))


@router.get("/me", operation_id="get_session")
def me(viewer: ViewerDep, settings: SettingsDep) -> SessionOut:
    return _session(viewer, settings)


# --- Real sign-in (Cognito) -----------------------------------------------------------------
# A person signs in on Cognito's own page; the browser brings back their ID token, and this
# exchanges it for Lane's session token. Everything after that (permissions, decision authority,
# the audit's names) is the same as for any other sign-in.

# When someone is in several groups, the first of these they are in is their role
ROLE_ORDER: tuple[str, ...] = ("senior_underwriter", "underwriter", "admin", "ops", "viewer")


class CognitoIn(BaseModel):
    id_token: str


class SignInConfigOut(BaseModel):
    """How the sign-in page should sign people in."""

    demo: bool
    cognito: bool


@router.get("/config", operation_id="get_sign_in_config")
def sign_in_config(settings: SettingsDep) -> SignInConfigOut:
    return SignInConfigOut(demo=settings.demo_login, cognito=bool(settings.cognito_user_pool_id))


def _issuer(settings: Settings) -> str:
    return (
        f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/"
        f"{settings.cognito_user_pool_id}"
    )


@lru_cache(maxsize=4)
def _keys(jwks_url: str) -> jwt.PyJWKClient:
    # Keys are cached; an unknown key id (after Cognito rotates) fetches the set again
    return jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)


def cognito_claims(id_token: str, settings: Settings) -> dict[str, Any]:
    """The claims of a valid Cognito ID token for this pool and app; 401 otherwise."""
    issuer = _issuer(settings)
    try:
        key = _keys(f"{issuer}/.well-known/jwks.json").get_signing_key_from_jwt(id_token)
        claims: dict[str, Any] = jwt.decode(
            id_token,
            key.key,
            algorithms=["RS256"],
            audience=settings.cognito_client_id,
            issuer=issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign-in could not be verified") from e
    if claims.get("token_use") != "id":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not an ID token")
    return claims


def role_for(groups: list[str]) -> str | None:
    return next((r for r in ROLE_ORDER if r in groups), None)


@router.post("/cognito", operation_id="cognito_login")
def cognito_login(body: CognitoIn, settings: SettingsDep) -> SessionOut:
    if not settings.cognito_user_pool_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cognito sign-in is not configured")
    claims = cognito_claims(body.id_token, settings)
    raw: object = claims.get("cognito:groups") or []
    groups = [str(g) for g in cast(list[object], raw)] if isinstance(raw, list) else []
    role = role_for(groups)
    if role is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This account has no role in Lane yet; ask an administrator"
        )
    name = str(claims.get("name") or claims.get("email") or claims["sub"])
    viewer = Viewer(role, signed_in=True, name=name)
    return _session(viewer, settings, issue_token(role, settings, name=name))


def token_of(authorization: str | None, access_token: str | None) -> str | None:
    """The sign-in token a request carries: a Bearer header, or the URL's for img and SSE."""
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
    return access_token or None
