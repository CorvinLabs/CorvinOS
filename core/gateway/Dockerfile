# ADR-0007 Phase 4 — Corvin Gateway OCI image
#
# Build: docker build -t corvin-gateway:latest \
#          -f core/gateway/Dockerfile .
# (the repo root is the build context; both corvin-gateway and forge
# sources are referenced relative to it.)
# Run:   docker run --rm -p 8000:8000 \
#          -v $HOME/.corvin:/var/lib/corvin \
#          -e CORVIN_HOME=/var/lib/corvin \
#          corvin-gateway:latest
#
# Notes
# -----
# * Slim base — the gateway has no need for system C toolchains at
#   runtime. cryptography wheels ship with pre-built binaries.
# * Non-root user with a stable uid/gid so the operator can chown
#   the bind-mounted CORVIN_HOME to match.
# * /healthz is the documented liveness probe; the chart wires it.
#
# What this image does NOT include
# --------------------------------
# * Any engine binary (claude / codex). The gateway dispatches via
#   the engine layer at request time; for production deployments
#   the engine sidecar runs separately (Phase 4 only ships the
#   gateway).
# * bwrap. The sandbox is a forge / skill-forge concern; gateway-
#   side runs go through the engine layer's own sandbox model.

FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Corvin Gateway"
LABEL org.opencontainers.image.description="Multi-tenant REST API for Corvin (ADR-0007)"
LABEL org.opencontainers.image.source="https://github.com/veegee82/Corvin"
LABEL org.opencontainers.image.licenses="see repo LICENSE"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Non-root runtime user. The chart's securityContext mirrors this.
RUN groupadd --system --gid 4711 corvin \
 && useradd --system --uid 4711 --gid 4711 --home /var/lib/corvin corvin \
 && mkdir -p /var/lib/corvin/tenants/_default/global \
 && chown -R corvin:corvin /var/lib/corvin

WORKDIR /opt/corvin-gateway

# Install runtime deps first (cached layer)
COPY --chown=corvin:corvin core/gateway/requirements.txt \
                             /opt/corvin-gateway/requirements.txt
RUN pip install --no-cache-dir -r /opt/corvin-gateway/requirements.txt

# Plugin code -- two packages: the gateway proper and the forge
# subset it depends on at import time (tenants module + paths +
# security_events). Build context is the repo root.
COPY --chown=corvin:corvin core/gateway/corvin_gateway \
                             /opt/corvin-gateway/corvin_gateway
COPY --chown=corvin:corvin operator/forge/forge \
                             /opt/corvin-gateway/forge

# Cross-PYTHONPATH so `from forge.tenants import ...` resolves.
ENV PYTHONPATH=/opt/corvin-gateway

EXPOSE 8000
USER corvin
ENV CORVIN_HOME=/var/lib/corvin

# uvicorn is the documented production entry point. Operators
# overlay --workers, --proxy-headers, --forwarded-allow-ips via
# the chart values.
ENTRYPOINT ["uvicorn", "corvin_gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
