# Production multi-stage OCI image for yoda-public (MOAI OAI-PMH + static landing pages).

FROM ubuntu:24.04 AS builder

ARG YODA_MOAI_REPO="https://github.com/UtrechtUniversity/yoda-moai.git"
ARG YODA_MOAI_REF="development"

ENV DEBIAN_FRONTEND="noninteractive"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      gcc \
      git \
      libxml2-dev \
      libxslt1-dev \
      python3 \
      python3-dev \
      python3-pip \
      python3.12-venv && \
    rm -rf /var/lib/apt/lists/*

RUN --mount=type=secret,id=extra_ca,target=/tmp/extra-ca.crt \
    if [ -s /tmp/extra-ca.crt ]; then \
      cp /tmp/extra-ca.crt /usr/local/share/ca-certificates/extra-ca.crt && update-ca-certificates; \
    fi && \
    git clone --depth 1 --branch "${YODA_MOAI_REF}" "${YODA_MOAI_REPO}" /src/yoda-moai

RUN python3 -m venv /opt/yoda/venv && \
    /opt/yoda/venv/bin/pip install --no-cache-dir --upgrade pip gunicorn && \
    for f in /src/yoda-moai/pyproject.toml /src/yoda-moai/setup.cfg /src/yoda-moai/setup.py; do \
      if [ -f "$f" ]; then sed -i "/mod-wsgi/d" "$f"; fi; \
    done && \
    SETUPTOOLS_SCM_PRETEND_VERSION_FOR_MOAI="${YODA_MOAI_VERSION:-0.0.0.dev0}" \
      /opt/yoda/venv/bin/pip install --no-cache-dir /src/yoda-moai


FROM ubuntu:24.04 AS runtime

LABEL org.opencontainers.image.title="yoda-public" \
      org.opencontainers.image.vendor="Utrecht University" \
      org.opencontainers.image.description="Production stateless OCI image for Yoda Public (MOAI + landing pages)"

ENV DEBIAN_FRONTEND="noninteractive" \
    PATH="/opt/yoda/venv/bin:${PATH}" \
    YODA_MOAI_CONFIG="/etc/yoda/moai-settings.ini"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      libxml2 \
      libxslt1.1 \
      python3 && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 yoda && \
    useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin yoda && \
    mkdir -p /var/www/moai/metadata /var/www/landingpages /etc/yoda && \
    chown -R yoda:yoda /var/www/moai /var/www/landingpages /etc/yoda

COPY --from=builder --chown=yoda:yoda /opt/yoda/venv /opt/yoda/venv
COPY --chmod=0755 entrypoint.sh /usr/local/bin/yoda-public-entrypoint.sh

USER yoda
WORKDIR /var/www/moai

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/oai/ || exit 1

ENTRYPOINT ["/usr/local/bin/yoda-public-entrypoint.sh"]
