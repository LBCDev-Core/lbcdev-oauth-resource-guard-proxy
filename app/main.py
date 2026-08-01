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

_client = httpx.AsyncClient(timeout=None)

HOP_BY_HOP = {
    "connection", "keep-alive", "transfer-encoding", "upgrade",
    "content-length", "host",
}


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
