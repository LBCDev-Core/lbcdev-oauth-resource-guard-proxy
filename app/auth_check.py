"""Validación del token de acceso (JWT firmado con RS256 por el servidor OAuth).

Política (todo lo que no cumpla devuelve 401, nunca 500):
- Solo RS256. `none`, HS* y cualquier otro algoritmo se rechazan antes de mirar la firma.
- La cabecera debe traer `kid` y la firma se comprueba con *esa* clave del JWKS.
- Obligatorios: `exp`, `iss` (== AUTH_ISSUER) y `aud` (== RESOURCE_URL, o una lista que lo contenga).
- Si el servidor OAuth no responde y no hay JWKS en caché, 503 (no 500 ni 401: el token puede ser bueno).
"""
import base64
import json
import logging
import os
import threading
import time
from urllib.parse import urlparse

import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError
from fastapi import Header, HTTPException

AUTH_ISSUER = os.environ["AUTH_ISSUER"]
RESOURCE_URL = os.environ["RESOURCE_URL"]
JWKS_URL = f"{AUTH_ISSUER}/.well-known/jwks.json"

ALLOWED_ALGORITHMS = ["RS256"]
# El servidor OAuth emite tokens de ~0,5 KB; un límite holgado evita trabajar con basura enorme.
MAX_TOKEN_BYTES = 8192
JWKS_TTL_SECONDS = 3600
# Mínimo entre refrescos forzados del JWKS: un token inválido no puede convertir el proxy
# en un amplificador de peticiones hacia el servidor OAuth.
JWKS_MIN_FORCED_REFRESH_SECONDS = 30
# Tras fallar un refresco con JWKS en caché se sigue usando la copia y se reintenta pronto.
JWKS_RETRY_AFTER_FAILURE_SECONDS = 60

_parsed = urlparse(RESOURCE_URL)
RESOURCE_METADATA_URL = f"{_parsed.scheme}://{_parsed.netloc}/.well-known/oauth-protected-resource"

log = logging.getLogger("auth_check")
_jwt = JsonWebToken(ALLOWED_ALGORITHMS)
_jwks_cache = {"keys": None, "fetched_at": 0, "last_forced": None, "retry_at": 0}
_jwks_lock = threading.Lock()


class JwksUnavailable(Exception):
    """No hay JWKS en caché y no se pudo descargar."""


def _download_jwks():
    resp = httpx.get(JWKS_URL, timeout=5)
    resp.raise_for_status()
    return JsonWebKey.import_key_set(resp.json())


def fetch_jwks(force: bool = False):
    with _jwks_lock:
        now = time.time()
        cached = _jwks_cache["keys"]
        if force:
            last = _jwks_cache["last_forced"]
            if last is not None and now - last < JWKS_MIN_FORCED_REFRESH_SECONDS and cached is not None:
                return cached
            _jwks_cache["last_forced"] = now
            due = True
        else:
            due = cached is None or (
                now - _jwks_cache["fetched_at"] > JWKS_TTL_SECONDS and now >= _jwks_cache["retry_at"]
            )
        if not due:
            return cached
        try:
            _jwks_cache["keys"] = _download_jwks()
            _jwks_cache["fetched_at"] = now
        except (httpx.HTTPError, ValueError, JoseError, KeyError, TypeError) as exc:
            log.warning("No se pudo descargar el JWKS (%s)", type(exc).__name__)
            if cached is None:
                raise JwksUnavailable from exc
            _jwks_cache["retry_at"] = now + JWKS_RETRY_AFTER_FAILURE_SECONDS
            return cached
        return _jwks_cache["keys"]


def unauthorized():
    return HTTPException(
        status_code=401,
        detail="Token ausente o inválido.",
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{RESOURCE_METADATA_URL}"'},
    )


def service_unavailable():
    return HTTPException(
        status_code=503,
        detail="Servidor de autorización no disponible.",
        headers={"Retry-After": "5"},
    )


def _bearer_token(authorization):
    # RFC 7235: el nombre del esquema no distingue mayúsculas.
    if not authorization:
        raise unauthorized()
    scheme, sep, token = authorization.partition(" ")
    if not sep or scheme.lower() != "bearer" or not token or token != token.strip():
        raise unauthorized()
    if len(token) > MAX_TOKEN_BYTES:
        raise unauthorized()
    return token


def _peek_header(token: str) -> dict:
    """Cabecera sin verificar, solo para elegir clave y descartar algoritmos no permitidos."""
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise unauthorized()
    try:
        raw = base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4))
        header = json.loads(raw)
    except ValueError:  # base64, UTF-8 y JSON inválidos
        raise unauthorized()
    if not isinstance(header, dict):
        raise unauthorized()
    kid = header.get("kid")
    if header.get("alg") not in ALLOWED_ALGORITHMS or not isinstance(kid, str) or not kid:
        raise unauthorized()
    return header


def _find_key(key_set, kid: str):
    for key in key_set.keys:
        if key.kid == kid:
            return key
    return None


def _verify_signature(token: str, key):
    """Claims con la firma ya verificada, o None si no valida."""
    if key is None:
        return None
    try:
        return _jwt.decode(
            token,
            key,
            claims_options={
                "exp": {"essential": True},
                "iss": {"essential": True},
                "aud": {"essential": True},
            },
        )
    except (JoseError, ValueError, TypeError, KeyError, UnicodeError):
        return None


def _validate_claims(claims) -> None:
    try:
        claims.validate()  # exp, nbf e iat con sus tipos; las esenciales deben existir
    except (JoseError, ValueError, TypeError, KeyError):
        raise unauthorized()
    if claims.get("iss") != AUTH_ISSUER:
        raise unauthorized()
    aud = claims.get("aud")
    if isinstance(aud, list):
        if RESOURCE_URL not in aud:
            raise unauthorized()
    elif aud != RESOURCE_URL:
        raise unauthorized()


def verify_token(authorization: str = Header(None)):
    token = _bearer_token(authorization)
    kid = _peek_header(token)["kid"]

    try:
        key_set = fetch_jwks()
        claims = _verify_signature(token, _find_key(key_set, kid))
        if claims is None:
            # kid nuevo o misma clave rotada: un único refresco acotado (ver JWKS_MIN_FORCED_REFRESH_SECONDS).
            key_set = fetch_jwks(force=True)
            claims = _verify_signature(token, _find_key(key_set, kid))
    except JwksUnavailable:
        raise service_unavailable()
    if claims is None:
        raise unauthorized()

    _validate_claims(claims)
    return claims
