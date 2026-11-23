# Despliegue y rollback del resource guard

Procedimiento para aplicar esta versión. **Nada de esto se ejecuta solo**: lo lanza una persona,
instancia por instancia. La imagen `resource-guard:latest` la usan varios stacks (`portainer-mcp-with-guard`,
`rabbitmq-mcp-with-guard`, `mcp-grafana-guard`, …): reconstruirla no actualiza los contenedores en marcha.

## Qué cambia respecto a la versión anterior

- Dependencias actualizadas y bloqueadas con hashes (Authlib, Starlette/FastAPI, cryptography).
- Validación JWT estricta: solo RS256, `kid`, `exp`, `iss` y `aud` obligatorios.
- JWKS caído sin caché: `503` en vez de `500`. Refrescos del JWKS limitados a uno cada 30 s.
- La imagen corre como `app` (uid 10001) y trae `HEALTHCHECK`.
- Sin cambios en variables de entorno, puertos ni volúmenes: el compose de cada stack **no** hay que tocarlo.

## Antes de empezar (5 min)

1. La PR está fusionada y el CI en verde (`tests`, `lock`, `audit`, `docker`).
2. Anota el estado actual de cada stack que usa la imagen:
   `docker ps --filter ancestor=resource-guard:latest --format '{{.Names}}\t{{.Image}}\t{{.Status}}'`
3. Conserva la imagen actual con una etiqueta fija (esto es lo que hace posible el rollback):
   ```bash
   docker tag resource-guard:latest resource-guard:previo-$(date +%Y%m%d)
   docker image ls resource-guard
   ```
4. Comprueba que los tokens reales traen lo que ahora es obligatorio. Con un token de tu cliente
   (sin pegarlo en ningún sitio compartido): decodifica la cabecera y el payload con
   `cut -d. -f1,2 | tr '.' '\n' | base64 -d` y mira que hay `alg: RS256`, `kid`, `exp`, `iss`, `aud`.
   Los emite `lbcdev-oauth-server`, que siempre los pone.

## Desplegar

1. Construir la imagen nueva, etiquetada con el commit:
   ```bash
   cd resource-guard && git pull
   docker build --label org.opencontainers.image.revision=$(git rev-parse HEAD) -t resource-guard:latest .
   ```
2. Redesplegar **un solo stack primero** (el de menos impacto, p. ej. `mcp-grafana-guard`). En Portainer:
   Stacks → el stack → *Update the stack* / recrear contenedor sin hacer pull.
3. Verificar ese stack (ver más abajo). Si está bien, repetir con los demás, uno a uno.

## Verificar (por instancia)

```bash
docker inspect <contenedor> --format '{{ .State.Health.Status }}'                       # healthy
docker inspect <contenedor> --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'  # commit desplegado
docker exec <contenedor> id                                                              # uid=10001(app)
curl -si https://<recurso>/mcp | head -5                                                 # 401 + www-authenticate
# Con un token válido (desde el cliente real, p. ej. conectar el conector en Claude.ai y hacer una llamada):
#   debe responder 200 / text/event-stream
docker logs --since 10m <contenedor> | grep -c ' 500 '                                  # 0
```

Señales de problema: `unhealthy`, `401` con tokens que antes valían (comprobar `exp`/`kid`/`aud` del token),
`503` persistentes (el guard no llega a `AUTH_ISSUER`; comprobar DNS y red del contenedor).

## Rollback

Criterio: cualquier `401` inesperado a clientes reales, `unhealthy` o errores 5xx que no existían antes.

```bash
docker tag resource-guard:previo-AAAAMMDD resource-guard:latest
# Redesplegar (recrear) cada stack afectado desde Portainer, sin pull.
docker inspect <contenedor> --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'  # vuelve al commit anterior
```

No hay datos ni migraciones: el rollback es solo volver a la imagen anterior. Al terminar, abre una
incidencia con el motivo antes de volver a intentarlo.
