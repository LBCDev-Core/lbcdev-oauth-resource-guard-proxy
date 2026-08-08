# lbcdev-lab-oauth-resource-guard-proxy

Proxy validador de tokens OAuth, reutilizable: la misma imagen protege
cualquier recurso propio verificando un JWT emitido por
[`lbcdev-lab-oauth-server`](https://github.com/Luinux81/lbcdev-lab-oauth-server)
(o cualquier authorization server compatible con OAuth 2.1 + JWKS público).
La configuración por recurso (qué protege, contra qué upstream) entra por
variables de entorno — la ruta expuesta en sí no (ver limitación conocida).

Cada recurso protegido es una instancia distinta de esta misma imagen, con
sus propias variables de entorno. No hay límite de cuántas instancias puedes
correr en el mismo servidor — normalmente, una por recurso.

## Stack

- FastAPI + `httpx` (proxy streaming, cliente async)
- `authlib` (verificación JWT contra JWKS)

## Cómo funciona

1. Descarga y cachea el JWKS del `AUTH_ISSUER` (refresco automático si aparece
   un `kid` desconocido)
2. Verifica firma, expiración y que `aud` coincide exactamente con `RESOURCE_URL`
3. Si es válido, reenvía la petición a `UPSTREAM_URL` (aplicando
   `UPSTREAM_EXTRA_HEADERS` si está definida), incluyendo streaming/SSE
4. Si no hay token o es inválido, responde `401` con `WWW-Authenticate`
   apuntando a su propio `/.well-known/oauth-protected-resource`

## Variables de entorno

| Variable | Obligatoria | Ejemplo |
|---|---|---|
| `RESOURCE_URL` | sí | `https://mcp.example.com/mcp` |
| `AUTH_ISSUER` | sí | `https://auth.example.com` |
| `UPSTREAM_URL` | sí | `http://host.docker.internal:8000/mcp` (upstream en el host o en otro stack) o `http://nombre-del-servicio:8000/mcp` (upstream en la misma red Docker interna) |
| `UPSTREAM_HOST_HEADER` | no | `127.0.0.1:8000` — fuerza la cabecera `Host` hacia el upstream, necesario si este valida por `Host` (protección anti DNS-rebinding) |
| `UPSTREAM_EXTRA_HEADERS` | no | Cabeceras fijas a inyectar/sobreescribir hacia el upstream, una por línea (`Cabecera:valor`). Sobreescribe cualquier cabecera entrante con el mismo nombre — útil para upstreams que exigen credenciales propias además del JWT que ya valida `guard` |

## Endpoints

| Método | Ruta | Función |
|---|---|---|
| GET | `/healthz` | Comprobación de salud |
| GET | `/.well-known/oauth-protected-resource` | Metadata RFC 9728 |
| GET/POST/DELETE | `/mcp` | Proxy real hacia `UPSTREAM_URL`, streaming transparente |

## ⚠️ Limitación conocida: la ruta `/mcp` está hardcodeada

A diferencia de `RESOURCE_URL`/`UPSTREAM_URL`, la ruta que expone el proxy
(`/mcp`) y los métodos aceptados (`GET`, `POST`, `DELETE`) están fijos en
`app/main.py` (`@app.api_route("/mcp", ...)`), no son configurables por
entorno. Válido mientras todo lo que protejas exponga su endpoint real en
`/mcp` (caso típico de servidores MCP). Si algún recurso futuro usa otra
ruta (`/sse`, `/rpc`, etc.), hay que editar el decorador antes de construir
la imagen para ese caso, o generalizar la ruta.

## Despliegue de una instancia nueva (proteger un recurso adicional)

Mismo servidor, misma imagen ya construida — solo hace falta un stack nuevo
con variables distintas. No requiere clonar el repo de nuevo si ya está
presente en el servidor.

```yaml
services:
  guard:
    image: resource-guard:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:PUERTO_LIBRE:8000"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - RESOURCE_URL=https://TU-DOMINIO/mcp
      - AUTH_ISSUER=https://auth.example.com
      - UPSTREAM_URL=http://host.docker.internal:PUERTO_DEL_MCP_REAL/mcp
      # - UPSTREAM_HOST_HEADER=127.0.0.1:PUERTO_DEL_MCP_REAL  # solo si el upstream valida Host
      # - UPSTREAM_EXTRA_HEADERS=Cabecera-Fija:valor           # una entrada por línea, ver tabla arriba
```

Si el recurso a proteger vive en la misma red Docker interna que `guard`
(en vez de en el host o en otro stack), omite `extra_hosts` y usa
`UPSTREAM_URL=http://nombre-del-servicio:PUERTO/mcp` directamente.

Luego, vhost Nginx del recurso apuntando al proxy en vez de al MCP directo.

## Despliegue en un servidor nuevo, desde cero

### 1. Acceso de lectura al repo (deploy key)

Si el repo es privado, usa una deploy key propia, de solo lectura — no
reutilices tu clave personal ni un token de acceso personal.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_resource-guard -C "deploy-key-<nombre-del-servidor>" -N ""
cat ~/.ssh/id_ed25519_resource-guard.pub
```

Cópiala en GitHub → este repo → **Settings → Deploy keys → Add deploy key**
→ deja **"Allow write access" desmarcado**.

```bash
cat >> ~/.ssh/config << 'EOF'

Host github-resource-guard
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_resource-guard
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
```

Verifica antes de clonar:
```bash
ssh -T git@github-resource-guard
```

Clona:
```bash
git clone git@github-resource-guard:<owner>/<repo>.git resource-guard
cd resource-guard
```

### 2. Construir la imagen

```bash
docker build -t resource-guard:latest .
```

Una sola imagen sirve para todas las instancias/recursos que quieras
proteger en ese servidor — no se reconstruye por cada recurso, solo cuando
cambia el código.

### 3. Desplegar la primera instancia

Ver plantilla de compose en "Despliegue de una instancia nueva" arriba.

### 4. Vhost Nginx real, plantilla probada (streaming + anti DNS-rebinding)

```nginx
server {
    server_name TU-DOMINIO;

    location / {
        proxy_pass http://127.0.0.1:PUERTO_DEL_GUARD;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Authorization $http_authorization;

        # Streamable HTTP mantiene conexiones abiertas — necesario para MCP
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
        proxy_buffering off;
    }

    listen [::]:443 ssl;
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/TU-DOMINIO/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/TU-DOMINIO/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}

server {
    if ($host = TU-DOMINIO) {
        return 301 https://$host$request_uri;
    }
    listen 80;
    listen [::]:80;
    server_name TU-DOMINIO;
    return 404;
}
```

*`proxy_set_header Authorization` es crítico — sin él, el token nunca llega
al proxy. `proxy_buffering off` evita que Nginx acumule el streaming antes de
enviarlo.*

```bash
sudo ln -s /etc/nginx/sites-available/TU-DOMINIO /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d TU-DOMINIO
```

### 5. Verificar

```bash
# Sin token — debe dar 401 con www-authenticate
curl -si https://TU-DOMINIO/mcp

# Con token válido para ese resource — debe dar 200, content-type: text/event-stream
curl -si --max-time 5 https://TU-DOMINIO/mcp -H "Authorization: Bearer TOKEN"
```

## Actualizar el código en un despliegue existente

El código se reconstruye **una vez**; luego hay que redesplegar **cada**
stack que use esta imagen — actualizar uno no actualiza los demás
automáticamente.

```bash
cd resource-guard   # el clon en el servidor, no el repo de trabajo local
git pull
docker build -t resource-guard:latest .
```
Luego, en tu herramienta de gestión de contenedores (p. ej. Portainer): por
cada stack afectado, redeploy sin volver a hacer pull de imagen — la imagen
es local.