import os
import time
from urllib.parse import urlparse

import httpx
from authlib.jose import JsonWebKey, jwt
from authlib.jose.errors import JoseError
from fastapi import Header, HTTPException

AUTH_ISSUER = os.environ["AUTH_ISSUER"]
RESOURCE_URL = os.environ["RESOURCE_URL"]
JWKS_URL = f"{AUTH_ISSUER}/.well-known/jwks.json"

_parsed = urlparse(RESOURCE_URL)
RESOURCE_METADATA_URL = f"{_parsed.scheme}://{_parsed.netloc}/.well-known/oauth-protected-resource"

_jwks_cache = {"keys": None, "fetched_at": 0}


def fetch_jwks(force: bool = False):
    now = time.time()
    if force or _jwks_cache["keys"] is None or now - _jwks_cache["fetched_at"] > 3600:
        resp = httpx.get(JWKS_URL, timeout=5)
        resp.raise_for_status()
        _jwks_cache["keys"] = JsonWebKey.import_key_set(resp.json())
        _jwks_cache["fetched_at"] = now
    return _jwks_cache["keys"]


def unauthorized():
    return HTTPException(
        status_code=401,
        detail="Token ausente o inválido.",
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{RESOURCE_METADATA_URL}"'},
    )


def verify_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise unauthorized()
    token = authorization[len("Bearer "):]

    try:
        claims = jwt.decode(token, fetch_jwks())
        claims.validate()
    except JoseError:
        try:
            claims = jwt.decode(token, fetch_jwks(force=True))
            claims.validate()
        except JoseError:
            raise unauthorized()

    if claims.get("iss") != AUTH_ISSUER:
        raise unauthorized()
    if claims.get("aud") != RESOURCE_URL:
        raise unauthorized()

    return claims
