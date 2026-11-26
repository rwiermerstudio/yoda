# Production multi-stage OCI image for yoda-portal (non-root, immutable, externalized config).
# TLS termination is handled by the external ingress/reverse proxy contract.

FROM ubuntu:24.04 AS builder

ARG YODA_PORTAL_REPO="https://github.com/UtrechtUniversity/yoda-portal.git"
ARG YODA_PORTAL_REF="development"

ENV DEBIAN_FRONTEND="noninteractive"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      gcc \
      git \
      python3 \
      python3-dev \
      python3-pip \
      python3.12-venv && \
    rm -rf /var/lib/apt/lists/*

RUN --mount=type=secret,id=extra_ca,target=/tmp/extra-ca.crt \
    if [ -s /tmp/extra-ca.crt ]; then \
      cp /tmp/extra-ca.crt /usr/local/share/ca-certificates/extra-ca.crt && update-ca-certificates; \
    fi && \
    git clone --depth 1 --branch "${YODA_PORTAL_REF}" "${YODA_PORTAL_REPO}" /src/yoda-portal && \
    rm -rf /src/yoda-portal/.git

RUN python3 -m venv /opt/yoda/venv && \
    /opt/yoda/venv/bin/pip install --no-cache-dir --upgrade pip gunicorn && \
    grep -vi '^mod-wsgi' /src/yoda-portal/requirements.txt > /tmp/portal-requirements.txt && \
    /opt/yoda/venv/bin/pip install --no-cache-dir -r /tmp/portal-requirements.txt


FROM ubuntu:24.04 AS runtime

LABEL org.opencontainers.image.title="yoda-portal" \
      org.opencontainers.image.vendor="Utrecht University" \
      org.opencontainers.image.description="Production stateless OCI image for Yoda Portal"

ENV DEBIAN_FRONTEND="noninteractive" \
    PATH="/opt/yoda/venv/bin:${PATH}" \
    YODA_PORTAL_CONFIG="/etc/yoda/flask.cfg" \
    YODA_PORTAL_BIND="0.0.0.0:8080" \
    YODA_PORTAL_WORKERS="4" \
    YODA_PORTAL_THREADS="4" \
    YODA_PORTAL_TIMEOUT="60"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      python3 && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 yoda && \
    useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin yoda && \
    mkdir -p /var/www/yoda/config /var/www/yoda/themes /var/www/portal-sessions /etc/yoda && \
    chown -R yoda:yoda /var/www/yoda /var/www/portal-sessions /etc/yoda

COPY --from=builder --chown=yoda:yoda /opt/yoda/venv /opt/yoda/venv
COPY --from=builder --chown=yoda:yoda /src/yoda-portal /var/www/yoda
COPY --chmod=0755 entrypoint.sh /usr/local/bin/yoda-portal-entrypoint.sh

USER yoda
WORKDIR /var/www/yoda

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/ || exit 1

ENTRYPOINT ["/usr/local/bin/yoda-portal-entrypoint.sh"]
