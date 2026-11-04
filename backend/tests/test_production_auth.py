"""Production sign-in: secrets come from Secrets Manager, the API answers only signed-in people,
the demo people can be turned off, and a Cognito ID token is exchanged for Lane's own session
only when it is genuine and the person has a role."""

import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from lane.api import auth
from lane.api.auth import issue_token
from lane.api.main import create_app
from lane.config import Settings, get_settings, with_secrets

POOL, CLIENT, REGION = "us-east-1_TestPool", "test-client-id", "us-east-1"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


# --- Secrets Manager ---------------------------------------------------------------------


def test_secrets_fill_the_key_the_session_secret_and_the_database_password() -> None:
    stored = {
        "lane/app": {"ANTHROPIC_API_KEY": "sk-test", "LANE_SESSION_SECRET": "s3cret"},
        "lane/db": {"username": "lane", "password": "p@ss/word"},
    }
    base = Settings(
        secrets_id="lane/app",
        db_secret_id="lane/db",
        database_url="postgresql+psycopg://lane:{password}@db:5432/lane?sslmode=require",
    )
    s = with_secrets(base, fetch=lambda sid: stored[sid])
    assert s.anthropic_api_key is not None and s.anthropic_api_key.get_secret_value() == "sk-test"
    assert s.session_secret.get_secret_value() == "s3cret"
    # Special characters in the password are escaped so the URL stays valid
    assert s.database_url == "postgresql+psycopg://lane:p%40ss%2Fword@db:5432/lane?sslmode=require"


def test_no_secret_ids_means_no_lookup() -> None:
    def never(_: str) -> dict[str, str]:
        raise AssertionError("looked up a secret")

    base = Settings()
    assert with_secrets(base, fetch=never) is base


def test_a_database_url_without_the_placeholder_is_refused() -> None:
    base = Settings(db_secret_id="lane/db", database_url="postgresql+psycopg://lane@db/lane")
    with pytest.raises(RuntimeError, match=r"\{password\}"):
        with_secrets(base, fetch=lambda _: {"password": "x"})


# --- Requiring sign-in -------------------------------------------------------------------


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch, db: Session) -> Iterator[TestClient]:
    monkeypatch.setenv("LANE_REQUIRE_SIGN_IN", "true")
    monkeypatch.setenv("LANE_DEMO_LOGIN", "false")
    monkeypatch.setenv("LANE_SESSION_SECRET", "production-secret")
    monkeypatch.setenv("LANE_COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("LANE_COGNITO_CLIENT_ID", CLIENT)
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c


def test_without_a_sign_in_the_api_refuses_everything_but_health_and_sign_in(
    production: TestClient,
) -> None:
    assert production.get("/api/v1/submissions").status_code == 401
    assert production.get("/api/v1/submissions/abc").status_code == 401
    assert production.get("/api/v1/health").status_code == 200
    assert production.get("/api/v1/auth/config").json() == {"demo": False, "cognito": True}


def test_a_token_signed_with_the_development_secret_is_refused(production: TestClient) -> None:
    forged = issue_token("admin", Settings(session_secret="dev-only-secret-change-me"))  # pyright: ignore[reportArgumentType]
    r = production.get("/api/v1/submissions", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


def test_a_valid_token_is_let_through_in_the_header_and_the_url(production: TestClient) -> None:
    token = issue_token("underwriter", get_settings(), name="Test Person")
    assert (
        production.get(
            "/api/v1/submissions", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 200
    )
    assert production.get(f"/api/v1/submissions?access_token={token}").status_code == 200


def test_demo_people_are_off(production: TestClient) -> None:
    assert production.get("/api/v1/auth/people").status_code == 404
    assert production.post("/api/v1/auth/demo-login", json={"role": "admin"}).status_code == 404


# --- Cognito -----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cognito's key set, served locally: the test key pair stands in for the pool's."""
    keys = SimpleNamespace(
        get_signing_key_from_jwt=lambda _t: SimpleNamespace(key=KEY.public_key())
    )
    monkeypatch.setattr(auth, "_keys", lambda _url: keys)


def id_token(**over: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": "u-1",
        "iss": ISSUER,
        "aud": CLIENT,
        "iat": now,
        "exp": now + 3600,
        "token_use": "id",
        "email": "reviewer@example.com",
        "name": "Rita Reviewer",
        "cognito:groups": ["underwriter"],
    } | over
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "test"})


def test_a_genuine_id_token_becomes_a_lane_session(production: TestClient) -> None:
    r = production.post("/api/v1/auth/cognito", json={"id_token": id_token()})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "underwriter" and body["name"] == "Rita Reviewer" and body["token"]
    # The Lane token then works everywhere
    ok = production.get("/api/v1/submissions", headers={"Authorization": f"Bearer {body['token']}"})
    assert ok.status_code == 200


@pytest.mark.parametrize(
    "over",
    [
        {"aud": "another-app"},
        {"iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Other"},
        {"exp": int(time.time()) - 60},
        {"token_use": "access"},
        {"sub": None},
    ],
    ids=["wrong app", "wrong pool", "expired", "access token", "no subject"],
)
def test_a_token_that_is_not_genuine_is_refused(
    production: TestClient, over: dict[str, Any]
) -> None:
    r = production.post("/api/v1/auth/cognito", json={"id_token": id_token(**over)})
    assert r.status_code == 401


def test_a_token_signed_by_another_key_is_refused(production: TestClient) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    forged = jwt.encode(
        {
            "sub": "x",
            "iss": ISSUER,
            "aud": CLIENT,
            "iat": now,
            "exp": now + 60,
            "token_use": "id",
            "cognito:groups": ["admin"],
        },
        other,
        algorithm="RS256",
    )
    assert production.post("/api/v1/auth/cognito", json={"id_token": forged}).status_code == 401


def test_no_role_is_refused_and_the_most_senior_role_wins(production: TestClient) -> None:
    none = production.post(
        "/api/v1/auth/cognito", json={"id_token": id_token(**{"cognito:groups": None})}
    )
    assert none.status_code == 403
    two = production.post(
        "/api/v1/auth/cognito",
        json={"id_token": id_token(**{"cognito:groups": ["underwriter", "senior_underwriter"]})},
    )
    assert two.json()["role"] == "senior_underwriter"


# --- Logging in every process ------------------------------------------------------------


def test_the_worker_logs_json_with_personal_data_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    """The alarms read the worker's JSON logs, and its logs must mask personal data too."""
    from lane import worker

    seen: dict[str, Any] = {}
    monkeypatch.setattr(worker, "configure_logging", lambda **kw: seen.update(kw))
    monkeypatch.setenv("LANE_LOG_JSON", "true")
    monkeypatch.setenv("LANE_RUN_MODE", "inline")  # returns at once, after setting up logging
    get_settings.cache_clear()
    assert worker.main() == 0
    assert seen["json"] is True and seen["pii"] == get_settings().pii_attributes
