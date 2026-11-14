import os
import httpx
from fastapi import FastAPI, Depends, Request
from fastapi.responses import StreamingResponse
from auth_check import verify_token

app = FastAPI()

RESOURCE_URL = os.environ["RESOURCE_URL"]
AUTH_ISSUER = os.environ["AUTH_ISSUER"]
UPSTREAM_URL = os.environ["UPSTREAM_URL"]
UPSTREAM_HOST_HEADER = os.environ.get("UPSTREAM_HOST_HEADER")
UPSTREAM_EXTRA_HEADERS_RAW = os.environ.get("UPSTREAM_EXTRA_HEADERS", "")

DEFAULT_RESOURCE_PATH = "/mcp"
HEALTH_PATH = "/healthz"
METADATA_PATH = "/.well-known/oauth-protected-resource"

_client = httpx.AsyncClient(timeout=None)

HOP_BY_HOP = {
    "connection", "keep-alive", "transfer-encoding", "upgrade",
    "content-length", "host",
}


def _parse_extra_headers(raw: str) -> dict[str, str]:
    headers = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        headers[key.strip()] = value.strip()
    return headers


UPSTREAM_EXTRA_HEADERS = _parse_extra_headers(UPSTREAM_EXTRA_HEADERS_RAW)


def _resolve_resource_path(raw: str | None) -> str:
    """Ruta pública del proxy. Sin definir (o vacía) es /mcp; si es inválida,
    falla al arrancar en vez de escuchar en una ruta distinta de la esperada."""
    path = (raw or "").strip()
    if not path:
        return DEFAULT_RESOURCE_PATH
    if not path.startswith("/"):
        raise ValueError(f"RESOURCE_PATH debe empezar por '/': {path!r}")
    if any(c in path for c in "?#{}") or any(c.isspace() for c in path):
        raise ValueError(
            f"RESOURCE_PATH no puede contener '?', '#', '{{', '}}' ni espacios: {path!r}"
        )
    if path.rstrip("/") in (HEALTH_PATH, METADATA_PATH):
        raise ValueError(f"RESOURCE_PATH no puede coincidir con una ruta reservada: {path!r}")
    return path


RESOURCE_PATH = _resolve_resource_path(os.environ.get("RESOURCE_PATH"))


@app.get(HEALTH_PATH)
def healthz():
    return {"status": "ok", "resource": RESOURCE_URL}


@app.get(METADATA_PATH)
def protected_resource_metadata():
    return {
        "resource": RESOURCE_URL,
        "authorization_servers": [AUTH_ISSUER],
        "bearer_methods_supported": ["header"],
    }


@app.api_route(RESOURCE_PATH, methods=["GET", "POST", "DELETE"])
async def mcp_proxy(request: Request, claims=Depends(verify_token)):
    body = await request.body()
    forward_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP
    }
    if UPSTREAM_HOST_HEADER:
        forward_headers["host"] = UPSTREAM_HOST_HEADER
    if UPSTREAM_EXTRA_HEADERS:
        override_keys = {k.lower() for k in UPSTREAM_EXTRA_HEADERS}
        forward_headers = {
            k: v for k, v in forward_headers.items() if k.lower() not in override_keys
        }
        forward_headers.update(UPSTREAM_EXTRA_HEADERS)

    upstream_request = _client.build_request(
        method=request.method,
        url=UPSTREAM_URL,
        headers=forward_headers,
        content=body,
    )
    upstream_response = await _client.send(upstream_request, stream=True)
    response_headers = {
        k: v for k, v in upstream_response.headers.items() if k.lower() not in HOP_BY_HOP
    }
    return StreamingResponse(
        upstream_response.aiter_raw(),
        status_code=upstream_response.status_code,
        headers=response_headers,
        background=upstream_response.aclose,
    )