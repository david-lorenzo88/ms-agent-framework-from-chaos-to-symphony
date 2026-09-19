# One image, both processes. DevUI stays on loopback and is reverse-proxied by
# the showcase, so the container exposes a single port - which is all Azure
# Container Apps gives an app anyway.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Bind every interface: the platform's ingress cannot reach loopback.
    CHAOS_HOST=0.0.0.0 \
    SHOWCASE_PORT=8000 \
    DEVUI_PORT=8080 \
    # Single origin: the browser only ever sees port 8000.
    CHAOS_PROXY_DEVUI=1 \
    # No keys, no network, no cost. Override only with the provider extras built in.
    CHAOS_PROVIDER=offline \
    # The package is pip-installed, so the site does not sit next to the module.
    CHAOS_WEB_DIR=/app/web

WORKDIR /app

# Dependencies first, so editing the app does not re-resolve the whole tree.
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Include the OpenAI provider (which also serves Azure OpenAI) so the deployed
# app can be switched to a real model with an environment variable instead of
# a rebuild. It adds a few MB and is inert unless CHAOS_PROVIDER says otherwise
# - without it, setting CHAOS_PROVIDER=azure silently runs scripted.
ARG INSTALL_EXTRAS=".[openai]"
RUN pip install --no-cache-dir "${INSTALL_EXTRAS}"

COPY web/ ./web/
COPY scripts/ ./scripts/
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

# The platform probes this; it is this app's own liveness and deliberately does
# not depend on DevUI. (/health is proxied through to DevUI.)
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4).status==200 else 1)"

CMD ["./docker-entrypoint.sh"]
