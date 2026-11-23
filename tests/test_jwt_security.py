"""Regresión de seguridad de la validación JWT: todo token que no cumpla la política
debe dar 401 (nunca 500 ni 200) y no debe llegar al upstream."""
import base64
import hashlib
import hmac
import json
import sys
import time

import httpx
import pytest
from authlib.jose import JsonWebKey, jwt
from fastapi.testclient import TestClient

from conftest import AUTH_ISSUER, RESOURCE_URL, _KEY, make_token

PEM_PRIVATE = _KEY.as_pem(is_private=True)
PEM_PUBLIC = _KEY.as_pem(is_private=False)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def now():
    return int(time.time())


def base_claims(**overrides):
    claims = {"iss": AUTH_ISSUER, "aud": RESOURCE_URL, "iat": now(), "exp": now() + 300}
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not ...}


def signed(claims, header=None):
    """RS256 con la clave buena. Se firma con el PEM porque, con un JWK, authlib
    sobrescribe el `kid` de la cabecera y no se podrían probar kid ausentes o ajenos."""
    header = {"alg": "RS256", "kid": "test"} if header is None else header
    return jwt.encode(header, claims, PEM_PRIVATE).decode()


def forged(header, claims, secret=None):
    h, p = b64(json.dumps(header).encode()), b64(json.dumps(claims).encode())
    sig = b""
    if secret is not None:
        sig = hmac.new(secret, f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{b64(sig or b'x')}"


def get(client, authorization):
    value = authorization.encode("latin-1") if isinstance(authorization, str) else authorization
    return client.get("/mcp", headers={"Authorization": value})


@pytest.fixture
def client(load_app):
    main, seen = load_app()
    return TestClient(main.app, raise_server_exceptions=False), seen


def assert_rejected(resp, seen):
    assert resp.status_code == 401, resp.text
    assert resp.headers["www-authenticate"].startswith('Bearer resource_metadata="')
    assert seen == []


REJECTED = {
    # Claims obligatorios y su valor
    "sin exp": lambda: signed(base_claims(exp=...)),
    "sin iss": lambda: signed(base_claims(iss=...)),
    "sin aud": lambda: signed(base_claims(aud=...)),
    "exp caducado": lambda: signed(base_claims(exp=now() - 10)),
    "exp texto": lambda: signed(base_claims(exp="mañana")),
    "exp nulo": lambda: signed(base_claims(exp=None)),
    "nbf futuro": lambda: signed(base_claims(nbf=now() + 3600)),
    "iss ajeno": lambda: signed(base_claims(iss="https://evil.example.test")),
    "aud ajeno": lambda: signed(base_claims(aud="https://otro.example.test/mcp")),
    "aud lista sin el recurso": lambda: signed(base_claims(aud=["https://a.test", "https://b.test"])),
    # Algoritmos
    "alg none": lambda: forged({"alg": "none", "kid": "test"}, base_claims()),
    "alg NONE": lambda: forged({"alg": "NONE", "kid": "test"}, base_claims()),
    "HS256 con la clave pública como secreto": lambda: forged({"alg": "HS256", "kid": "test"}, base_claims(), secret=PEM_PUBLIC),
    "HS256 con secreto vacío": lambda: forged({"alg": "HS256", "kid": "test"}, base_claims(), secret=b""),
    "alg numérico": lambda: forged({"alg": 5, "kid": "test"}, base_claims()),
    "alg ausente": lambda: forged({"kid": "test"}, base_claims()),
    # Claves
    "kid ajeno (firma buena)": lambda: signed(base_claims(), {"alg": "RS256", "kid": "no-existe"}),
    "sin kid (firma buena)": lambda: signed(base_claims(), {"alg": "RS256"}),
    "kid numérico": lambda: forged({"alg": "RS256", "kid": 5}, base_claims()),
    "kid lista": lambda: forged({"alg": "RS256", "kid": ["test"]}, base_claims()),
    "jku en cabecera": lambda: forged({"alg": "RS256", "kid": "test", "jku": "http://evil.test/jwks"}, base_claims()),
    # Firma y estructura
    "firma alterada": lambda: signed(base_claims())[:-4] + "AAAA",
    "payload alterado": lambda: (lambda t: t.split(".")[0] + "." + b64(json.dumps(base_claims(aud="x")).encode()) + "." + t.split(".")[2])(signed(base_claims())),
    "sin puntos": lambda: "abc",
    "cuatro segmentos": lambda: "a.b.c.d",
    "segmentos vacíos": lambda: "..",
    "base64 inválido": lambda: "!!!.@@@.###",
    "cabecera no JSON": lambda: b64(b"no-json") + "." + b64(b"{}") + ".c2ln",
    "cabecera lista JSON": lambda: b64(b"[1]") + "." + b64(b"{}") + ".c2ln",
    "payload no JSON": lambda: b64(b'{"alg":"RS256","kid":"test"}') + "." + b64(b"no-json") + ".c2ln",
    "payload lista JSON": lambda: b64(b'{"alg":"RS256","kid":"test"}') + "." + b64(b"[1,2]") + ".c2ln",
    "token gigante": lambda: "A" * 200_000,
}


@pytest.mark.parametrize("name", REJECTED)
def test_rejected_token_gives_401(client, name):
    cli, seen = client
    assert_rejected(get(cli, "Bearer " + REJECTED[name]()), seen)


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Bearer ", "Bearer  ", "Basic dXNlcjpwYXNz", "Token abc", "BearerX abc", "Bearer a b"],
)
def test_malformed_authorization_header_gives_401(client, header):
    cli, seen = client
    resp = cli.get("/mcp") if header is None else get(cli, header)
    assert_rejected(resp, seen)


def test_non_ascii_bytes_in_header_give_401(client):
    cli, seen = client
    assert_rejected(get(cli, "Bearer caf\xe9.x.y"), seen)


def test_double_space_before_token_gives_401(client):
    cli, seen = client
    assert_rejected(get(cli, "Bearer  " + make_token()), seen)


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER", "bEaReR"])
def test_scheme_name_is_case_insensitive(client, scheme):
    cli, seen = client
    assert get(cli, f"{scheme} {make_token()}").status_code == 200
    assert len(seen) == 1


def test_audience_as_list_containing_the_resource_is_accepted(client):
    cli, seen = client
    assert get(cli, "Bearer " + signed(base_claims(aud=[RESOURCE_URL, "https://otro.test"]))).status_code == 200


def test_valid_token_passes_and_claims_are_exposed(client):
    cli, seen = client
    assert get(cli, "Bearer " + make_token()).status_code == 200
    assert len(seen) == 1


def test_only_rs256_is_allowed(load_app):
    load_app()
    assert sys.modules["auth_check"].ALLOWED_ALGORITHMS == ["RS256"]


# --- JWKS: disponibilidad y límites de refresco ---------------------------------

def test_jwks_unavailable_without_cache_gives_503_not_500(load_app, monkeypatch):
    main, seen = load_app(patch_jwks=False)

    def down(url, **kwargs):
        raise httpx.ConnectError("sin red", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", down)
    resp = TestClient(main.app, raise_server_exceptions=False).get(
        "/mcp", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "5"
    assert seen == []


def test_jwks_http_error_without_cache_gives_503(load_app, monkeypatch):
    main, _ = load_app(patch_jwks=False)
    monkeypatch.setattr(
        httpx, "get", lambda url, **kw: httpx.Response(500, request=httpx.Request("GET", url))
    )
    resp = TestClient(main.app, raise_server_exceptions=False).get(
        "/mcp", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert resp.status_code == 503


def test_jwks_garbage_without_cache_gives_503(load_app, monkeypatch):
    main, _ = load_app(patch_jwks=False)
    monkeypatch.setattr(
        httpx, "get", lambda url, **kw: httpx.Response(200, json={"nope": 1}, request=httpx.Request("GET", url))
    )
    resp = TestClient(main.app, raise_server_exceptions=False).get(
        "/mcp", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert resp.status_code == 503


def test_stale_jwks_keeps_serving_when_the_hourly_refresh_fails(load_app, jwks_server, monkeypatch):
    main, _ = load_app(patch_jwks=False)
    cli = TestClient(main.app, raise_server_exceptions=False)
    auth = {"Authorization": f"Bearer {make_token()}"}
    assert cli.get("/mcp", headers=auth).status_code == 200

    sys.modules["auth_check"]._jwks_cache["fetched_at"] -= 3601

    def down(url, **kwargs):
        raise httpx.ConnectError("sin red", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", down)
    assert cli.get("/mcp", headers=auth).status_code == 200


def test_forced_refresh_is_rate_limited(load_app, jwks_server):
    main, _ = load_app(patch_jwks=False)
    cli = TestClient(main.app, raise_server_exceptions=False)
    stranger = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "test"})
    bad = {"Authorization": f"Bearer {make_token(key=stranger)}"}

    for _ in range(10):
        assert cli.get("/mcp", headers=bad).status_code == 401
    # 1 descarga inicial + 1 refresco forzado: los otros 9 intentos no llegan al servidor OAuth.
    assert len(jwks_server.calls) == 2

    sys.modules["auth_check"]._jwks_cache["last_forced"] -= 31
    assert cli.get("/mcp", headers=bad).status_code == 401
    assert len(jwks_server.calls) == 3


def test_claim_errors_never_download_the_jwks_again(load_app, jwks_server):
    main, _ = load_app(patch_jwks=False)
    cli = TestClient(main.app, raise_server_exceptions=False)
    assert cli.get("/mcp", headers={"Authorization": f"Bearer {make_token()}"}).status_code == 200
    for token in (signed(base_claims(exp=...)), signed(base_claims(aud="x")), signed(base_claims(exp=1))):
        assert cli.get("/mcp", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert len(jwks_server.calls) == 1
