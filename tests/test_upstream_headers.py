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


def test_file_headers_override_client(load_app, token, tmp_path):
    path = tmp_path / "headers"
    path.write_text("X-Internal: fixture\n")
    main, seen = load_app(env={"UPSTREAM_EXTRA_HEADERS_FILE": str(path)})
    client = TestClient(main.app)
    assert client.post("/mcp", headers={"X-Internal": "spoof"}).status_code == 401
    assert not seen
    assert client.post("/mcp", headers={**auth(token), "x-internal": "spoof"}).status_code == 200
    assert seen[0].headers.get_list("x-internal") == ["fixture"]


def test_file_invalid_fails_closed(load_app, tmp_path):
    import pytest
    path = tmp_path / "headers"
    for content in (b"", b"bad line", b"X-A: a\nx-a: b", b"Host: x",
                    b"X-A: a\x00b", b"X-A: " + b"x" * 8192):
        path.write_bytes(content)
        with pytest.raises(RuntimeError, match="invalid upstream extra-header file"):
            load_app(env={"UPSTREAM_EXTRA_HEADERS_FILE": str(path)})
    path.unlink()
    with pytest.raises(RuntimeError, match="invalid upstream extra-header file"):
        load_app(env={"UPSTREAM_EXTRA_HEADERS_FILE": str(path)})


def test_file_and_env_conflict(load_app, tmp_path):
    import pytest
    path = tmp_path / "headers"
    path.write_text("X-A: fixture")
    with pytest.raises(RuntimeError, match="configure only one"):
        load_app(env={"UPSTREAM_EXTRA_HEADERS_FILE": str(path),
                      "UPSTREAM_EXTRA_HEADERS": "X-B: fixture"})
