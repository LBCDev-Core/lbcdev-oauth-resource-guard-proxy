# lbcdev-lab-oauth-resource-guard-proxy

Proxy validador de tokens OAuth, reutilizable: la misma imagen protege
cualquier recurso propio verificando un JWT emitido por
[`lbcdev-lab-oauth-server`](https://github.com/Luinux81/lbcdev-lab-oauth-server),
sin lógica específica de ningún servicio en el código — todo lo variable entra
por entorno.

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
| `UPSTREAM_HOST_HEADER` | no | `127.0.0.1:8000` — fuerza la cabecera `Host` hacia el upstream, necesario si este valida por `Host`/anti DNS-rebinding |

## Endpoints

| Método | Ruta | Función |
|---|---|---|
| GET | `/healthz` | Comprobación de salud |
| GET | `/.well-known/oauth-protected-resource` | Metadata RFC 9728 |
| GET/POST/DELETE | `/mcp` | Proxy real hacia `UPSTREAM_URL`, streaming transparente |

## Instancias desplegadas

| Stack Portainer | Protege | `RESOURCE_URL` |
|---|---|---|
| `mcp-grafana-guard` | `mcp-grafana` | `https://mcp-grafana.my-vps.lbcdev.es/mcp` |

## Despliegue

Build local, sin publicar a ningún registry:

```bash
docker build -t resource-guard:latest .
```

Cada recurso protegido es un stack Portainer independiente con la misma
imagen y variables de entorno propias.

## Estado

- [x] Validación JWT (firma, expiración, `aud`)
- [x] Proxy transparente con streaming (probado contra `mcp-grafana`)
- [ ] Segunda instancia (FreshRSS u otro)
