import pytest
from fastapi.testclient import TestClient

from conftest import RESOURCE_URL, UPSTREAM_URL, make_token

EXPECTED_CHALLENGE = (
    'Bearer resource_metadata="https://mcp.example.test/.well-known/oauth-protected-resource"'
)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_default_path_is_mcp(load_app, token):
    main, seen = load_app()
    assert main.RESOURCE_PATH == "/mcp"
    resp = TestClient(main.app).post("/mcp", headers=auth(token), json={"a": 1})
    assert resp.status_code == 200
    assert len(seen) == 1


@pytest.mark.parametrize("empty", ["", "   "])
def test_empty_value_falls_back_to_default(load_app, token, empty):
    main, _ = load_app(resource_path=empty)
    assert main.RESOURCE_PATH == "/mcp"
    assert TestClient(main.app).get("/mcp", headers=auth(token)).status_code == 200


def test_custom_path_replaces_default(load_app, token):
    main, seen = load_app(resource_path="/sse")
    client = TestClient(main.app)
    assert client.get("/sse", headers=auth(token)).status_code == 200
    assert client.get("/mcp", headers=auth(token)).status_code == 404
    assert len(seen) == 1
    # El upstream no depende de la ruta pública.
    assert str(seen[0].url) == UPSTREAM_URL


def test_nested_custom_path(load_app, token):
    main, _ = load_app(resource_path="/api/v1/rpc")
    assert TestClient(main.app).post("/api/v1/rpc", headers=auth(token)).status_code == 200


@pytest.mark.parametrize("path", [None, "/sse"])
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_methods_are_forwarded(load_app, token, path, method):
    main, seen = load_app(resource_path=path)
    resp = TestClient(main.app).request(method, path or "/mcp", headers=auth(token))
    assert resp.status_code == 200
    assert seen[0].method == method


@pytest.mark.parametrize("method", ["PUT", "PATCH"])
def test_other_methods_not_allowed(load_app, token, method):
    main, seen = load_app(resource_path="/sse")
    resp = TestClient(main.app).request(method, "/sse", headers=auth(token))
    assert resp.status_code == 405
    assert seen == []


@pytest.mark.parametrize("path", [None, "/sse"])
def test_missing_token_gives_401_with_challenge(load_app, path):
    main, seen = load_app(resource_path=path)
    resp = TestClient(main.app).get(path or "/mcp")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == EXPECTED_CHALLENGE
    assert seen == []


def test_invalid_tokens_are_rejected(load_app, other_key):
    main, seen = load_app(resource_path="/sse")
    client = TestClient(main.app)
    bad_tokens = {
        "firma": make_token(key=other_key),
        "expirado": make_token(exp=1),
        "audiencia": make_token(aud="https://otro.example.test/mcp"),
        "emisor": make_token(iss="https://otro-auth.example.test"),
        "basura": "no-es-un-jwt",
    }
    for name, bad in bad_tokens.items():
        resp = client.get("/sse", headers=auth(bad))
        assert resp.status_code == 401, name
        assert resp.headers["www-authenticate"] == EXPECTED_CHALLENGE, name
    assert seen == []


@pytest.mark.parametrize("path", [None, "/sse"])
def test_auxiliary_routes_unchanged(load_app, path):
    main, _ = load_app(resource_path=path)
    client = TestClient(main.app)
    assert client.get("/healthz").json() == {"status": "ok", "resource": RESOURCE_URL}
    meta = client.get("/.well-known/oauth-protected-resource").json()
    assert meta["resource"] == RESOURCE_URL
    assert meta["bearer_methods_supported"] == ["header"]


def test_streaming_body_and_status_pass_through(load_app, token):
    chunks = [b"event: message\ndata: uno\n\n", b"event: message\ndata: dos\n\n"]

    upstream = (202, {"content-type": "text/event-stream", "mcp-session-id": "abc"}, chunks)
    main, seen = load_app(resource_path="/sse", upstream=upstream)
    resp = TestClient(main.app).post("/sse", headers=auth(token), content=b'{"x":1}')
    assert resp.status_code == 202
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["mcp-session-id"] == "abc"
    assert resp.content == b"".join(chunks)
    assert seen[0].content == b'{"x":1}'


@pytest.mark.parametrize(
    "bad",
    [
        "sse",
        "/sse?x=1",
        "/sse#frag",
        "/items/{id}",
        "/con espacio",
        "/healthz",
        "/healthz/",
        "/.well-known/oauth-protected-resource",
    ],
)
def test_invalid_paths_fail_at_startup(load_app, bad):
    with pytest.raises(ValueError, match="RESOURCE_PATH"):
        load_app(resource_path=bad)
