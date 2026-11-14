from fastapi.testclient import TestClient


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_request_headers_reach_upstream_without_extras(load_app, token):
    main, seen = load_app()
    resp = TestClient(main.app).post(
        "/mcp", headers={**auth(token), "X-Custom": "abc"}, content=b"hola"
    )
    assert resp.status_code == 200
    sent = seen[0].headers
    assert sent["x-custom"] == "abc"
    assert sent["authorization"] == f"Bearer {token}"
    # Sin UPSTREAM_HOST_HEADER, el Host es el del upstream, no el del cliente.
    assert sent["host"] == "upstream.test:8000"


def test_host_header_is_forced(load_app, token):
    main, seen = load_app(env={"UPSTREAM_HOST_HEADER": "127.0.0.1:8000"})
    TestClient(main.app).get("/mcp", headers=auth(token))
    assert seen[0].headers["host"] == "127.0.0.1:8000"
    # El destino real no cambia: solo la cabecera.
    assert seen[0].url.host == "upstream.test"


def test_extra_headers_are_injected(load_app, token):
    main, seen = load_app(env={"UPSTREAM_EXTRA_HEADERS": "X-API-Key: secreto"})
    TestClient(main.app).get("/mcp", headers=auth(token))
    assert seen[0].headers["x-api-key"] == "secreto"


def test_extra_headers_override_incoming_case_insensitive(load_app, token):
    main, seen = load_app(env={"UPSTREAM_EXTRA_HEADERS": "X-API-Key: del-proxy"})
    TestClient(main.app).get("/mcp", headers={**auth(token), "x-api-key": "del-cliente"})
    assert seen[0].headers.get_list("x-api-key") == ["del-proxy"]


def test_extra_headers_can_replace_authorization(load_app, token):
    # El JWT se valida en el proxy; el upstream recibe su propia credencial.
    main, seen = load_app(env={"UPSTREAM_EXTRA_HEADERS": "Authorization: Bearer upstream-token"})
    resp = TestClient(main.app).get("/mcp", headers=auth(token))
    assert resp.status_code == 200
    assert seen[0].headers.get_list("authorization") == ["Bearer upstream-token"]


def test_extra_headers_do_not_bypass_auth(load_app):
    main, seen = load_app(env={"UPSTREAM_EXTRA_HEADERS": "Authorization: Bearer upstream-token"})
    resp = TestClient(main.app).get("/mcp")
    assert resp.status_code == 401
    assert seen == []


def test_extra_headers_multiline_parsing(load_app, token):
    raw = "\n".join(
        [
            "X-Uno: 1",
            "",
            "  X-Dos :  dos  ",
            "linea-sin-separador",
            "X-Url: http://interno:9000/ruta",
        ]
    )
    main, seen = load_app(env={"UPSTREAM_EXTRA_HEADERS": raw})
    assert main.UPSTREAM_EXTRA_HEADERS == {
        "X-Uno": "1",
        "X-Dos": "dos",
        "X-Url": "http://interno:9000/ruta",
    }
    TestClient(main.app).get("/mcp", headers=auth(token))
    sent = seen[0].headers
    assert (sent["x-uno"], sent["x-dos"], sent["x-url"]) == ("1", "dos", "http://interno:9000/ruta")


def test_host_and_extra_headers_together(load_app, token):
    main, seen = load_app(
        env={"UPSTREAM_HOST_HEADER": "127.0.0.1:8000", "UPSTREAM_EXTRA_HEADERS": "X-API-Key: k"}
    )
    TestClient(main.app).delete("/mcp", headers=auth(token))
    assert seen[0].headers["host"] == "127.0.0.1:8000"
    assert seen[0].headers["x-api-key"] == "k"
