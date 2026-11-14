import importlib
import os
import sys
import time
from pathlib import Path

import httpx
import pytest
from authlib.jose import JsonWebKey, jwt

# El código vive en app/ y se importa como módulos de primer nivel (igual que
# en la imagen Docker, donde app/ se copia a la raíz de trabajo).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

RESOURCE_URL = "https://mcp.example.test/mcp"
AUTH_ISSUER = "https://auth.example.test"
UPSTREAM_URL = "http://upstream.test:8000/mcp"

_KEY = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "test"})
_OTHER_KEY = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "test"})


def make_token(key=_KEY, **overrides):
    now = int(time.time())
    claims = {"iss": AUTH_ISSUER, "aud": RESOURCE_URL, "iat": now, "exp": now + 300}
    claims.update(overrides)
    return jwt.encode({"alg": "RS256", "kid": "test"}, claims, key).decode()


@pytest.fixture
def token():
    return make_token()


@pytest.fixture
def other_key():
    return _OTHER_KEY


@pytest.fixture
def load_app(monkeypatch):
    """Carga la app con el entorno indicado. Devuelve (main, peticiones al upstream).

    `upstream` es (status, cabeceras, trozos del cuerpo) de la respuesta simulada."""

    def _load(resource_path=None, upstream=None):
        monkeypatch.setenv("RESOURCE_URL", RESOURCE_URL)
        monkeypatch.setenv("AUTH_ISSUER", AUTH_ISSUER)
        monkeypatch.setenv("UPSTREAM_URL", UPSTREAM_URL)
        monkeypatch.delenv("UPSTREAM_HOST_HEADER", raising=False)
        monkeypatch.delenv("UPSTREAM_EXTRA_HEADERS", raising=False)
        if resource_path is None:
            monkeypatch.delenv("RESOURCE_PATH", raising=False)
        else:
            monkeypatch.setenv("RESOURCE_PATH", resource_path)

        for name in ("main", "auth_check"):
            sys.modules.pop(name, None)
        main = importlib.import_module("main")
        auth_check = sys.modules["auth_check"]

        # JWKS local: sin red.
        public_set = JsonWebKey.import_key_set({"keys": [_KEY.as_dict(is_private=False)]})
        monkeypatch.setattr(auth_check, "fetch_jwks", lambda force=False: public_set)

        seen = []
        status, headers, chunks = upstream or (200, {"content-type": "application/json"}, [b'{"ok":true}'])

        class _Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                for chunk in chunks:
                    yield chunk

        class _Upstream(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                await request.aread()
                seen.append(request)
                return httpx.Response(status, headers=headers, stream=_Body())

        monkeypatch.setattr(main, "_client", httpx.AsyncClient(transport=_Upstream()))
        return main, seen

    return _load
