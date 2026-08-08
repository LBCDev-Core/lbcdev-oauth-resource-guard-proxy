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


@app.get("/healthz")
def healthz():
    return {"status": "ok", "resource": RESOURCE_URL}


@app.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata():
    return {
        "resource": RESOURCE_URL,
        "authorization_servers": [AUTH_ISSUER],
        "bearer_methods_supported": ["header"],
    }


@app.api_route("/mcp", methods=["GET", "POST", "DELETE"])
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