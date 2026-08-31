# Corvin — production image
#
# Generic: knows nothing about a specific operator. Configuration is supplied
# at runtime via bind-mounted ${CORVIN_HOST_HOME} (mapped to /home/corvin)
# and via environment variables (see ops/.env.template).
#
# Build:
#   docker build -t ghcr.io/veegee82/corvinos:latest \
#                --build-arg CORVIN_REPO_REF=main \
#                -f ops/Dockerfile .
#
# Run:
#   docker compose -f ops/docker-compose.yml up -d

FROM ubuntu:24.04 AS base

LABEL org.opencontainers.image.title="Corvin"
LABEL org.opencontainers.image.description="Multi-tenant voice/chat agent OS (Apache-2.0)"
LABEL org.opencontainers.image.source="https://github.com/veegee82/Corvin"
LABEL org.opencontainers.image.licenses="Apache-2.0"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    CORVIN_HOME=/home/corvin/.corvin \
    CORVIN_VENV=/opt/corvin-venv \
    PYTHONPATH=/opt/corvin-repo:/opt/corvin-repo/operator \
    PATH="/opt/corvin-venv/bin:/home/corvin/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# ── system base ───────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl wget git gnupg jq sudo less \
        python3.12 python3.12-venv python3-pip \
        ffmpeg age sqlite3 bubblewrap supervisor tini \
        build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Node 20 (Ubuntu 24.04 ships node 18 which is below claude-code minimum)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get update && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# claude-code CLI (root-global, all users can use it)
RUN npm install -g @anthropic-ai/claude-code

# Non-root runtime user. UID 1000 matches the typical host user — bind-mounts
# from /opt/corvin/home land owned by corvin without chown gymnastics.
# Ubuntu 24.04 base ships a default user at uid 1000; we replace it.
RUN (userdel -r ubuntu 2>/dev/null || true) \
 && (groupdel ubuntu 2>/dev/null || true) \
 && groupadd --gid 1000 corvin \
 && useradd --uid 1000 --gid 1000 --home /home/corvin --shell /bin/bash --create-home corvin \
 && mkdir -p /opt/corvin-repo /opt/corvin-venv /var/log/corvin \
 && chown -R corvin:corvin /home/corvin /opt/corvin-repo /opt/corvin-venv /var/log/corvin

# ── application code ─────────────────────────────────────────────────────
# Source is copied from the build context (whatever the operator hands to
# `docker build`). .dockerignore filters out per-checkout state. To pin to
# a published tag instead, clone the repo locally and check it out before
# building, or use the bootstrap installer which does git-clone for you.
COPY --chown=corvin:corvin . /opt/corvin-repo

USER corvin
WORKDIR /home/corvin

# Python venv with all runtime deps (operator-side: bridges, voice, gateway).
# Placed under /opt/corvin-venv so it survives the /home/corvin bind-mount.
#
# python-multipart is REQUIRED by core/console/corvin_console/routes/voice.py
# (FastAPI UploadFile). Without it the whole corvin_console import dies at
# decorator-evaluation time and the console plugin is silently absent from
# the gateway's mount table — every /v1/console/* and /console/* returns 404.
# python-dotenv is openai's SDK transitive — vendor it explicitly so the
# voice/tts subprocess and STT provider load without a "module not found".
# anthropic + openai are the two engine SDKs the adapter spawns into.
RUN python3.12 -m venv /opt/corvin-venv \
 && /opt/corvin-venv/bin/pip install --no-cache-dir --upgrade pip wheel setuptools \
 && /opt/corvin-venv/bin/pip install --no-cache-dir \
        fastapi "uvicorn[standard]" pyyaml \
        prometheus-client cryptography "pyjwt[crypto]" \
        requests httpx \
        watchfiles \
        python-multipart python-dotenv \
        anthropic openai matplotlib \
 && /opt/corvin-venv/bin/pip install -e /opt/corvin-repo/core/gateway

# Node deps for each bridge daemon (per-channel package.json)
RUN set -eux; for d in /opt/corvin-repo/operator/bridges/*/; do \
      if [ -f "$d/package.json" ]; then \
        (cd "$d" && npm install --omit=dev --no-audit --no-fund) || true; \
      fi; \
    done

# Build the web-next/ console SPA at image-build time. The result lands at
# corvin_console/web-next/dist/ and is served by mount_static(). This is
# the only console frontend; without this build step the SPA is not mounted
# (the /v1/console REST API still works).
RUN set -eux; \
    cd /opt/corvin-repo/core/console/corvin_console/web-next \
 && npm install --no-audit --no-fund \
 && npm run build \
 && rm -rf node_modules

# ── runtime ─────────────────────────────────────────────────────────────
USER root

# Replace the Debian default config wholesale — our supervisord.conf is
# selfcontained ([supervisord], [unix_http_server] with 0770 perms,
# [rpcinterface], [supervisorctl] all present). Drop the empty
# /etc/supervisor/conf.d/ to ensure no stale include leaks in.
COPY ops/supervisord.conf /etc/supervisor/supervisord.conf
RUN rm -f /etc/supervisor/conf.d/*.conf
COPY ops/healthcheck.sh   /usr/local/bin/corvin-healthcheck
COPY ops/entrypoint.sh    /usr/local/bin/corvin-entrypoint
RUN chmod +x /usr/local/bin/corvin-healthcheck /usr/local/bin/corvin-entrypoint

# Console UI (8765), gateway REST (8000), Prometheus metrics (9090)
EXPOSE 8765 8000 9090

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD /usr/local/bin/corvin-healthcheck

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/corvin-entrypoint"]
CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
