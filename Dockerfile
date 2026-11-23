# Base fijada por versión Y por digest: el tag puede moverse, el digest no.
# Dependabot propone las actualizaciones (ver .github/dependabot.yml); a mano:
#   docker buildx imagetools inspect python:3.12.16-slim-trixie   (línea "Digest:")
FROM python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Usuario sin privilegios (uid fijo para poder reproducirlo fuera de Docker).
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app

WORKDIR /app
# Dependencias bloqueadas con hashes (requirements.txt se genera desde requirements.in).
COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt
COPY app/ .

USER app
EXPOSE 8000
# Salud: la app responde en /healthz. La imagen no trae curl, así que se usa Python.
# No reinicia el contenedor al fallar: es una señal para Docker, Portainer y el despliegue.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
