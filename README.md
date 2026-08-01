# lbcdev-lab-oauth-resource-guard-proxy

Proxy validador de tokens OAuth, reutilizable: la misma imagen protege
cualquier recurso propio verificando un JWT emitido por
[`lbcdev-lab-oauth-server`](https://github.com/Luinux81/lbcdev-lab-oauth-server).
La configuración por recurso (qué protege, contra qué upstream) entra por
variables de entorno — la ruta expuesta en sí no (ver limitación conocida).

## Stack

- FastAPI + `httpx` (proxy streaming, cliente async)
- `authlib` (verificación JWT contra JWKS)

## Cómo funciona

1. Descarga y cachea el JWKS del `AUTH_ISSUER` (refresco automático si aparece
   un `kid` desconocido)
2. Verifica firma, expiración y que `aud` coincide exactamente con `RESOURCE_URL`
3. Si es válido, reenvía la petición tal cual (incluye streaming/SSE) a `UPSTREAM_URL`
4. Si no hay token o es inválido, responde `401` con `WWW-Authenticate`
   apuntando a su propio `/.well-known/oauth-protected-resource`

## Variables de entorno

| Variable | Obligatoria | Ejemplo |
|---|---|---|
| `RESOURCE_URL` | sí | `https://mcp-grafana.my-vps.lbcdev.es/mcp` |
| `AUTH_ISSUER` | sí | `https://auth.lbcdev.es` |
| `UPSTREAM_URL` | sí | `http://host.docker.internal:8000/mcp` |
| `UPSTREAM_HOST_HEADER` | no | `127.0.0.1:8000` — fuerza la cabecera `Host` hacia el upstream, necesario si este valida por `Host` (protección anti DNS-rebinding, es el caso de `mcp-grafana`) |

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
`/mcp` (caso de todos los MCP servers vistos hasta ahora). Si algún recurso
futuro usa otra ruta (`/sse`, `/rpc`, etc.), hay que editar el decorador
antes de construir la imagen para ese caso, o generalizar la ruta —
candidato a PMT futuro si llega a hacer falta.

## Instancias desplegadas

| Stack Portainer | Protege | `RESOURCE_URL` |
|---|---|---|
| `mcp-grafana-guard` | `mcp-grafana` | `https://mcp-grafana.my-vps.lbcdev.es/mcp` |

## Despliegue de una instancia nueva (proteger un recurso adicional)

Mismo VPS, misma imagen ya construida — solo hace falta un stack de Portainer
nuevo con variables distintas. No requiere clonar el repo de nuevo si ya está
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
      - AUTH_ISSUER=https://auth.lbcdev.es
      - UPSTREAM_URL=http://host.docker.internal:PUERTO_DEL_MCP_REAL/mcp
      # - UPSTREAM_HOST_HEADER=127.0.0.1:PUERTO_DEL_MCP_REAL  # solo si el upstream valida Host
```

Luego, vhost Nginx del recurso apuntando al proxy en vez de al MCP directo —
usa como plantilla el de `mcp-grafana` (ver más abajo), cambiando dominio y
puerto.

## Despliegue en un VPS nuevo, desde cero

### 1. Acceso de lectura al repo (deploy key)

Este repo es privado. Usa una deploy key propia, de solo lectura — no
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
# Esperado: "Hi Luinux81/lbcdev-lab-oauth-resource-guard-proxy! You've successfully authenticated..."
```

Clona:
```bash
git clone git@github-resource-guard:Luinux81/lbcdev-lab-oauth-resource-guard-proxy.git resource-guard
cd resource-guard
```

### 2. Construir la imagen

```bash
docker build -t resource-guard:latest .
```

Una sola imagen sirve para todas las instancias/recursos que quieras
proteger en ese servidor — no se reconstruye por cada recurso, solo cuando
cambia el código.

### 3. Desplegar la primera instancia (Portainer, stack vía Web editor)

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
stack de Portainer que use esta imagen (hoy solo `mcp-grafana-guard`, más
adelante también el de FreshRSS, etc.) — actualizar uno no actualiza los
demás automáticamente.

```bash
cd resource-guard   # el clon en el servidor, no el repo de trabajo local
git pull
docker build -t resource-guard:latest .
```
Luego, en Portainer: por cada stack afectado → *Editor* → **"Update the
stack"** (sin marcar "Pull latest image" — la imagen es local).

## Estado

- [x] Validación JWT (firma, expiración, `aud`)
- [x] Proxy transparente con streaming (probado contra `mcp-grafana`)
- [x] Verificado end-to-end con conector real de Claude.ai (PMT 5)
- [ ] Ruta `/mcp` parametrizada (hoy hardcodeada, ver limitación arriba)
- [ ] Segunda instancia (FreshRSS u otro)