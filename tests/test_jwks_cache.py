import sys

from authlib.jose import JsonWebKey
from fastapi.testclient import TestClient

from conftest import AUTH_ISSUER, make_token


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_jwks_is_downloaded_once_and_cached(load_app, jwks_server, token):
    main, seen = load_app(patch_jwks=False)
    client = TestClient(main.app)
    for _ in range(3):
        assert client.get("/mcp", headers=auth(token)).status_code == 200
    assert jwks_server.calls == [f"{AUTH_ISSUER}/.well-known/jwks.json"]
    assert len(seen) == 3


def test_jwks_is_not_downloaded_without_token(load_app, jwks_server):
    main, _ = load_app(patch_jwks=False)
    assert TestClient(main.app).get("/mcp").status_code == 401
    assert jwks_server.calls == []


def test_jwks_is_refreshed_after_one_hour(load_app, jwks_server, token):
    main, _ = load_app(patch_jwks=False)
    client = TestClient(main.app)
    assert client.get("/mcp", headers=auth(token)).status_code == 200
    sys.modules["auth_check"]._jwks_cache["fetched_at"] -= 3601
    assert client.get("/mcp", headers=auth(token)).status_code == 200
    assert len(jwks_server.calls) == 2


def test_key_rotation_with_same_kid_forces_refresh(load_app, jwks_server, token):
    main, _ = load_app(patch_jwks=False)
    client = TestClient(main.app)
    assert client.get("/mcp", headers=auth(token)).status_code == 200

    # El servidor OAuth cambia la clave conservando el kid: la firma deja de
    # validar con la clave en caché y el proxy vuelve a descargar el JWKS.
    new_key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "test"})
    jwks_server.keys = [new_key]
    rotated = make_token(key=new_key)

    assert client.get("/mcp", headers=auth(rotated)).status_code == 200
    assert len(jwks_server.calls) == 2
    # La clave nueva queda en caché: no hay más descargas.
    assert client.get("/mcp", headers=auth(rotated)).status_code == 200
    assert len(jwks_server.calls) == 2


def test_key_rotation_with_new_kid_forces_refresh(load_app, jwks_server, token):
    main, _ = load_app(patch_jwks=False)
    client = TestClient(main.app, raise_server_exceptions=False)
    assert client.get("/mcp", headers=auth(token)).status_code == 200

    new_key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "nueva"})
    jwks_server.keys = [new_key]

    rotated = make_token(key=new_key)
    assert client.get("/mcp", headers=auth(rotated)).status_code == 200
    assert len(jwks_server.calls) == 2
    # La clave nueva queda en caché, y el token de la clave retirada deja de valer.
    assert client.get("/mcp", headers=auth(rotated)).status_code == 200
    assert len(jwks_server.calls) == 2
    assert client.get("/mcp", headers=auth(token)).status_code == 401


def test_token_with_unknown_kid_gives_401(load_app, jwks_server, token):
    main, seen = load_app(patch_jwks=False)
    client = TestClient(main.app, raise_server_exceptions=False)
    stranger = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "ajena"})

    resp = client.get("/mcp", headers=auth(make_token(key=stranger)))
    assert resp.status_code == 401
    assert "www-authenticate" in resp.headers
    assert seen == []
    # Un intento de refresco, igual que con cualquier token que no valida.
    assert len(jwks_server.calls) == 2


def test_invalid_token_triggers_one_refresh_then_401(load_app, jwks_server, token, other_key):
    # Comportamiento actual: cada token que no valida (firma o expiración)
    # provoca una descarga forzada del JWKS antes de responder 401.
    main, seen = load_app(patch_jwks=False)
    client = TestClient(main.app)
    assert client.get("/mcp", headers=auth(token)).status_code == 200
    assert len(jwks_server.calls) == 1

    assert client.get("/mcp", headers=auth(make_token(key=other_key))).status_code == 401
    assert len(jwks_server.calls) == 2
    assert client.get("/mcp", headers=auth(make_token(exp=1))).status_code == 401
    assert len(jwks_server.calls) == 3
    assert len(seen) == 1


def test_wrong_audience_does_not_refresh(load_app, jwks_server, token):
    main, _ = load_app(patch_jwks=False)
    client = TestClient(main.app)
    assert client.get("/mcp", headers=auth(token)).status_code == 200
    bad = make_token(aud="https://otro.example.test/mcp")
    assert client.get("/mcp", headers=auth(bad)).status_code == 401
    assert len(jwks_server.calls) == 1
