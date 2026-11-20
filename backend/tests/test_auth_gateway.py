from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from httpx import AsyncClient

from app.core.config import get_settings
from app.core.security import keyring
from tests.conftest import PASSWORD, login

settings = get_settings()


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(
    fixture_data,
    *,
    challenge: str,
    tenant: str | None = None,
    state: str | None = "state-123",
    nonce: str | None = "nonce-123",
    scope: str = "openid profile email offline_access",
    client_id: str | None = None,
    redirect_uri: str | None = None,
    prompt: str | None = None,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id or fixture_data.oidc_client_id,
        "redirect_uri": redirect_uri or fixture_data.oidc_redirect_uri,
        "scope": scope,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if tenant:
        params["tenant"] = tenant
    if state:
        params["state"] = state
    if nonce:
        params["nonce"] = nonce
    if prompt:
        params["prompt"] = prompt
    return f"/v1/oauth/authorize?{urlencode(params)}"


async def console_headers(client: AsyncClient, email: str) -> dict[str, str]:
    response = await login(client, email, PASSWORD)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


async def exchange_code(
    client: AsyncClient,
    fixture_data,
    code: str,
    verifier: str,
    *,
    redirect_uri: str | None = None,
) -> object:
    return await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": fixture_data.oidc_client_id,
            "client_secret": fixture_data.oidc_client_secret,
            "code": code,
            "redirect_uri": redirect_uri or fixture_data.oidc_redirect_uri,
            "code_verifier": verifier,
        },
    )


async def obtain_code_and_tokens(client: AsyncClient, fixture_data, *, email: str | None = None) -> dict:
    headers = await console_headers(client, email or fixture_data.tenant_a_user_email)
    verifier, challenge = pkce_pair()
    response = await client.get(
        authorize_url(fixture_data, challenge=challenge, tenant=fixture_data.tenant_a_slug),
        headers=headers,
    )
    assert response.status_code == 302, response.text
    location = urlparse(response.headers["location"])
    code = parse_qs(location.query)["code"][0]
    token = await exchange_code(client, fixture_data, code, verifier)
    assert token.status_code == 200, token.text
    return token.json()


async def test_authorize_redirects_anonymous_user_to_hosted_login(client: AsyncClient, fixture_data) -> None:
    _, challenge = pkce_pair()
    response = await client.get(
        authorize_url(fixture_data, challenge=challenge, tenant=fixture_data.tenant_a_slug)
    )
    assert response.status_code == 303
    location = urlparse(response.headers["location"])
    assert location.scheme == "http" and location.path == "/login"
    query = parse_qs(location.query)
    assert query["return_to"][0].startswith("/v1/oauth/authorize")
    assert query["app_name"][0]


async def test_authorize_rejects_unregistered_redirect_uri(client: AsyncClient, fixture_data) -> None:
    _, challenge = pkce_pair()
    response = await client.get(
        authorize_url(
            fixture_data,
            challenge=challenge,
            tenant=fixture_data.tenant_a_slug,
            redirect_uri="http://localhost:3003/evil/callback",
        )
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


async def test_authorize_honours_prompt_none_without_session(client: AsyncClient, fixture_data) -> None:
    _, challenge = pkce_pair()
    response = await client.get(
        authorize_url(
            fixture_data,
            challenge=challenge,
            tenant=fixture_data.tenant_a_slug,
            prompt="none",
        )
    )
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["error"][0] == "login_required"
    assert query["state"][0] == "state-123"


async def test_prompt_login_redirects_and_strips_prompt_from_return_to(
    client: AsyncClient, fixture_data
) -> None:
    headers = await console_headers(client, fixture_data.tenant_a_user_email)
    _, challenge = pkce_pair()
    response = await client.get(
        authorize_url(
            fixture_data,
            challenge=challenge,
            tenant=fixture_data.tenant_a_slug,
            prompt="login",
        ),
        headers=headers,
    )
    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert "prompt" not in query["return_to"][0]
    assert query["reauth"][0] == "1"


async def test_authorization_code_flow_issues_scoped_tokens(client: AsyncClient, fixture_data) -> None:
    headers = await console_headers(client, fixture_data.tenant_a_user_email)
    verifier, challenge = pkce_pair()
    response = await client.get(
        authorize_url(
            fixture_data,
            challenge=challenge,
            tenant=fixture_data.tenant_a_slug,
            nonce="nonce-abc",
        ),
        headers=headers,
    )
    assert response.status_code == 302, response.text
    redirect = urlparse(response.headers["location"])
    assert f"{redirect.scheme}://{redirect.netloc}{redirect.path}" == fixture_data.oidc_redirect_uri
    query = parse_qs(redirect.query)
    assert query["state"][0] == "state-123"

    token = await exchange_code(client, fixture_data, query["code"][0], verifier)
    assert token.status_code == 200, token.text
    body = token.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"] and body["refresh_token"] and body["id_token"]
    assert body["scope"] == "openid profile email offline_access"
    assert body["session_id"]

    id_claims = jwt.decode(
        body["id_token"],
        keyring().public_pem,
        algorithms=["RS256"],
        audience=fixture_data.oidc_client_id,
        issuer=settings.jwt_issuer,
    )
    assert id_claims["typ"] == "id"
    assert id_claims["nonce"] == "nonce-abc"
    assert id_claims["email"] == fixture_data.tenant_a_user_email
    assert id_claims["tenant_id"] == str(fixture_data.tenant_a_id)
    assert id_claims["tenant_slug"] == fixture_data.tenant_a_slug
    assert id_claims["app_id"] == str(fixture_data.application_id)
    assert id_claims["aud"] == fixture_data.oidc_client_id
    assert "tenant_owner" in id_claims["roles"]
    assert "tenants.read" in id_claims["permissions"]
    assert id_claims["sid"] == body["session_id"]

    userinfo = await client.get(
        "/v1/oauth/userinfo", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert userinfo.status_code == 200, userinfo.text
    info = userinfo.json()
    assert info["email"] == fixture_data.tenant_a_user_email
    assert info["tenant_id"] == str(fixture_data.tenant_a_id)
    assert info["tenant_slug"] == fixture_data.tenant_a_slug
    assert info["client_id"] == fixture_data.oidc_client_id
    assert "tenant_owner" in info["roles"]

    # An app token has its own audience and must never be accepted by the console.
    admin_api = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert admin_api.status_code == 401

    # ... and a console token must never be accepted as an application token.
    console_only = await client.get("/v1/oauth/userinfo", headers={"Authorization": headers["Authorization"]})
    assert console_only.status_code == 401


async def test_code_exchange_rejects_wrong_pkce_verifier(client: AsyncClient, fixture_data) -> None:
    headers = await console_headers(client, fixture_data.tenant_a_user_email)
    verifier, challenge = pkce_pair()
    response = await client.get(
        authorize_url(fixture_data, challenge=challenge, tenant=fixture_data.tenant_a_slug),
        headers=headers,
    )
    code = parse_qs(urlparse(response.headers["location"]).query)["code"][0]

    bad_verifier = secrets.token_urlsafe(64)
    token = await exchange_code(client, fixture_data, code, bad_verifier)
    assert token.status_code == 400
    assert token.json()["error"] == "invalid_grant"

    # PKCE mismatch does not burn the code: the correct verifier still works.
    good = await exchange_code(client, fixture_data, code, verifier)
    assert good.status_code == 200, good.text


async def test_code_exchange_rejects_redirect_uri_mismatch(client: AsyncClient, fixture_data) -> None:
    headers = await console_headers(client, fixture_data.tenant_a_user_email)
    verifier, challenge = pkce_pair()
    response = await client.get(
        authorize_url(fixture_data, challenge=challenge, tenant=fixture_data.tenant_a_slug),
        headers=headers,
    )
    code = parse_qs(urlparse(response.headers["location"]).query)["code"][0]

    token = await exchange_code(
        client,
        fixture_data,
        code,
        verifier,
        redirect_uri="http://localhost:3003/auth/callback?extra=1",
    )
    assert token.status_code == 400
    assert token.json()["error"] == "invalid_grant"


async def test_code_replay_is_rejected_and_revokes_sessions(client: AsyncClient, fixture_data) -> None:
    headers = await console_headers(client, fixture_data.tenant_a_user_email)
    verifier, challenge = pkce_pair()
    response = await client.get(
        authorize_url(fixture_data, challenge=challenge, tenant=fixture_data.tenant_a_slug),
        headers=headers,
    )
    code = parse_qs(urlparse(response.headers["location"]).query)["code"][0]

    first = await exchange_code(client, fixture_data, code, verifier)
    assert first.status_code == 200
    replay = await exchange_code(client, fixture_data, code, verifier)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"

    # The replayed code is treated as compromise: the issued refresh token is dead.
    refresh = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": fixture_data.oidc_client_id,
            "client_secret": fixture_data.oidc_client_secret,
            "refresh_token": first.json()["refresh_token"],
        },
    )
    assert refresh.status_code == 400


async def test_cross_tenant_access_is_denied(client: AsyncClient, fixture_data) -> None:
    headers = await console_headers(client, fixture_data.tenant_a_user_email)
    _, challenge = pkce_pair()
    response = await client.get(
        authorize_url(fixture_data, challenge=challenge, tenant=fixture_data.tenant_b_slug),
        headers=headers,
    )
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["error"][0] == "access_denied"
    assert query["state"][0] == "state-123"


async def test_invalid_scope_is_rejected(client: AsyncClient, fixture_data) -> None:
    headers = await console_headers(client, fixture_data.tenant_a_user_email)
    _, challenge = pkce_pair()
    response = await client.get(
        authorize_url(
            fixture_data,
            challenge=challenge,
            tenant=fixture_data.tenant_a_slug,
            scope="openid platform:root",
        ),
        headers=headers,
    )
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["error"][0] == "invalid_scope"


async def test_refresh_rotation_and_reuse_detection(client: AsyncClient, fixture_data) -> None:
    tokens = await obtain_code_and_tokens(client, fixture_data)

    rotated = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": fixture_data.oidc_client_id,
            "client_secret": fixture_data.oidc_client_secret,
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert rotated.status_code == 200, rotated.text
    newer = rotated.json()
    assert newer["refresh_token"] != tokens["refresh_token"]

    reuse = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": fixture_data.oidc_client_id,
            "client_secret": fixture_data.oidc_client_secret,
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert reuse.status_code == 400
    assert reuse.json()["error"] == "invalid_grant"

    # Reuse revokes the whole token family, including the freshly rotated token.
    after = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": fixture_data.oidc_client_id,
            "client_secret": fixture_data.oidc_client_secret,
            "refresh_token": newer["refresh_token"],
        },
    )
    assert after.status_code == 400

    userinfo = await client.get(
        "/v1/oauth/userinfo", headers={"Authorization": f"Bearer {newer['access_token']}"}
    )
    assert userinfo.status_code == 401


async def test_refresh_cannot_widen_scope(client: AsyncClient, fixture_data) -> None:
    headers = await console_headers(client, fixture_data.tenant_a_user_email)
    verifier, challenge = pkce_pair()
    response = await client.get(
        authorize_url(
            fixture_data,
            challenge=challenge,
            tenant=fixture_data.tenant_a_slug,
            scope="openid email",
        ),
        headers=headers,
    )
    code = parse_qs(urlparse(response.headers["location"]).query)["code"][0]
    tokens = (await exchange_code(client, fixture_data, code, verifier)).json()

    widened = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": fixture_data.oidc_client_id,
            "client_secret": fixture_data.oidc_client_secret,
            "refresh_token": tokens["refresh_token"],
            "scope": "openid email offline_access",
        },
    )
    assert widened.status_code == 400
    assert widened.json()["error"] == "invalid_scope"


async def test_embedded_login_issues_application_tokens(client: AsyncClient, fixture_data) -> None:
    response = await client.post(
        "/v1/oauth/embedded/login",
        json={
            "client_id": fixture_data.oidc_client_id,
            "client_secret": fixture_data.oidc_client_secret,
            "tenant": fixture_data.tenant_a_slug,
            "email": fixture_data.tenant_a_user_email,
            "password": PASSWORD,
            "scope": "openid profile email",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "authenticated"
    assert body["tenant"]["slug"] == fixture_data.tenant_a_slug
    assert body["application"]["id"] == str(fixture_data.application_id)
    assert body["user"]["email"] == fixture_data.tenant_a_user_email
    assert body["access_token"] and body["id_token"] and body["refresh_token"]

    userinfo = await client.get(
        "/v1/oauth/userinfo", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert userinfo.status_code == 200
    assert userinfo.json()["tenant_slug"] == fixture_data.tenant_a_slug


async def test_embedded_login_denies_wrong_tenant(client: AsyncClient, fixture_data) -> None:
    response = await client.post(
        "/v1/oauth/embedded/login",
        json={
            "client_id": fixture_data.oidc_client_id,
            "client_secret": fixture_data.oidc_client_secret,
            "tenant": fixture_data.tenant_b_slug,
            "email": fixture_data.tenant_a_user_email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "access_denied"


async def test_embedded_login_unknown_tenant_uses_oauth_envelope(client: AsyncClient, fixture_data) -> None:
    response = await client.post(
        "/v1/oauth/embedded/login",
        json={
            "client_id": fixture_data.oidc_client_id,
            "client_secret": fixture_data.oidc_client_secret,
            "tenant": "no-such-tenant",
            "email": fixture_data.tenant_a_user_email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


async def test_embedded_login_rejects_bad_password(client: AsyncClient, fixture_data) -> None:
    response = await client.post(
        "/v1/oauth/embedded/login",
        json={
            "client_id": fixture_data.oidc_client_id,
            "client_secret": fixture_data.oidc_client_secret,
            "tenant": fixture_data.tenant_a_slug,
            "email": fixture_data.tenant_a_user_email,
            "password": "Wrong!Password#2026",
        },
    )
    assert response.status_code == 401


async def test_embedded_login_rejects_bad_client_secret(client: AsyncClient, fixture_data) -> None:
    response = await client.post(
        "/v1/oauth/embedded/login",
        json={
            "client_id": fixture_data.oidc_client_id,
            "client_secret": "not-the-secret",
            "tenant": fixture_data.tenant_a_slug,
            "email": fixture_data.tenant_a_user_email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


async def test_discovery_documents_publish_gateway_endpoints(client: AsyncClient) -> None:
    response = await client.get("/.well-known/openid-configuration")
    assert response.status_code == 200
    body = response.json()
    assert body["issuer"] == settings.jwt_issuer
    assert body["authorization_endpoint"].endswith("/v1/oauth/authorize")
    assert body["token_endpoint"].endswith("/v1/oauth/token")
    assert body["userinfo_endpoint"].endswith("/v1/oauth/userinfo")
    assert body["end_session_endpoint"].endswith("/v1/oauth/logout")
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert "tenant_slug" in body["claims_supported"]

    oauth = await client.get("/.well-known/oauth-authorization-server")
    assert oauth.status_code == 200
    assert "authorization_code" in oauth.json()["grant_types_supported"]


async def test_admin_can_manage_login_clients(
    client: AsyncClient, fixture_data, admin_headers: dict[str, str]
) -> None:
    created = await client.post(
        f"/v1/applications/{fixture_data.application_id}/clients",
        headers=admin_headers,
        json={
            "name": "Managed test client",
            "client_type": "confidential",
            "redirect_uris": [fixture_data.oidc_redirect_uri],
            "post_logout_redirect_uris": ["http://localhost:3003/"],
            "scopes": ["openid", "profile", "email"],
        },
    )
    assert created.status_code == 201, created.text
    client_body = created.json()
    assert client_body["client_id"].startswith("basa_")
    assert client_body["client_secret"].startswith("bsc_")

    listing = await client.get(
        f"/v1/applications/{fixture_data.application_id}/clients", headers=admin_headers
    )
    assert listing.status_code == 200
    assert client_body["client_id"] in {row["client_id"] for row in listing.json()}

    detail = await client.get(f"/v1/applications/{fixture_data.application_id}", headers=admin_headers)
    assert client_body["client_id"] in {row["client_id"] for row in detail.json()["clients"]}

    rotated = await client.post(
        f"/v1/applications/{fixture_data.application_id}/clients/{client_body['client_id']}/rotate-secret",
        headers=admin_headers,
    )
    assert rotated.status_code == 200
    assert rotated.json()["client_secret"] != client_body["client_secret"]

    # A rotation invalidates the old secret at the token endpoint.
    stale = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_body["client_id"],
            "client_secret": client_body["client_secret"],
            "code": "irrelevant",
            "redirect_uri": fixture_data.oidc_redirect_uri,
        },
    )
    assert stale.status_code == 401

    disabled = await client.delete(
        f"/v1/applications/{fixture_data.application_id}/clients/{client_body['client_id']}",
        headers=admin_headers,
    )
    assert disabled.status_code == 204


async def test_disabling_a_client_revokes_its_tokens(
    client: AsyncClient, fixture_data, admin_headers: dict[str, str]
) -> None:
    created = await client.post(
        f"/v1/applications/{fixture_data.application_id}/clients",
        headers=admin_headers,
        json={
            "name": "Revocable client",
            "client_type": "confidential",
            "redirect_uris": [fixture_data.oidc_redirect_uri],
            "scopes": ["openid", "profile", "email"],
        },
    )
    assert created.status_code == 201, created.text
    new_client = created.json()

    headers = await console_headers(client, fixture_data.tenant_a_user_email)
    verifier, challenge = pkce_pair()
    authorize = await client.get(
        authorize_url(
            fixture_data,
            challenge=challenge,
            tenant=fixture_data.tenant_a_slug,
            client_id=new_client["client_id"],
            scope="openid profile email",
        ),
        headers=headers,
    )
    assert authorize.status_code == 302, authorize.text
    code = parse_qs(urlparse(authorize.headers["location"]).query).get("code", [None])[0]
    assert code, authorize.headers["location"]
    token = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": new_client["client_id"],
            "client_secret": new_client["client_secret"],
            "code": code,
            "redirect_uri": fixture_data.oidc_redirect_uri,
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200, token.text
    access_token = token.json()["access_token"]

    disabled = await client.delete(
        f"/v1/applications/{fixture_data.application_id}/clients/{new_client['client_id']}",
        headers=admin_headers,
    )
    assert disabled.status_code == 204

    userinfo = await client.get("/v1/oauth/userinfo", headers={"Authorization": f"Bearer {access_token}"})
    assert userinfo.status_code == 401


async def test_public_clients_require_pkce_and_have_no_secret(
    client: AsyncClient, fixture_data, admin_headers: dict[str, str]
) -> None:
    created = await client.post(
        f"/v1/applications/{fixture_data.application_id}/clients",
        headers=admin_headers,
        json={
            "name": "Public SPA client",
            "client_type": "public",
            "redirect_uris": ["http://localhost:3003/auth/callback"],
            "scopes": ["openid", "profile", "email"],
            "pkce_required": False,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["client_secret"] is None
    assert body["pkce_required"] is True

    rotated = await client.post(
        f"/v1/applications/{fixture_data.application_id}/clients/{body['client_id']}/rotate-secret",
        headers=admin_headers,
    )
    assert rotated.status_code == 422

    # A public client skips the shared secret but still needs a PKCE challenge.
    verifier, challenge = pkce_pair()
    headers = await console_headers(client, fixture_data.tenant_a_user_email)
    authorize = await client.get(
        authorize_url(
            fixture_data,
            challenge=challenge,
            tenant=fixture_data.tenant_a_slug,
            client_id=body["client_id"],
            scope="openid profile email",
        ),
        headers=headers,
    )
    code = parse_qs(urlparse(authorize.headers["location"]).query).get("code", [None])[0]
    assert code, authorize.headers["location"]
    token = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": body["client_id"],
            "code": code,
            "redirect_uri": fixture_data.oidc_redirect_uri,
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200, token.text


async def test_lookup_of_unknown_client_fails(client: AsyncClient, fixture_data) -> None:
    _, challenge = pkce_pair()
    response = await client.get(
        authorize_url(
            fixture_data,
            challenge=challenge,
            tenant=fixture_data.tenant_a_slug,
            client_id="basa_does_not_exist",
        )
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"
