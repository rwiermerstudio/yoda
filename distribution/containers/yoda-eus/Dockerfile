# Production multi-stage OCI image for yoda-external-user-service (EUS).

FROM ubuntu:24.04 AS builder

ARG YODA_EUS_REPO="https://github.com/UtrechtUniversity/yoda-external-user-service.git"
ARG YODA_EUS_REF="development"

ENV DEBIAN_FRONTEND="noninteractive"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      gcc \
      git \
      libpq-dev \
      python3 \
      python3-dev \
      python3-pip \
      python3.12-venv && \
    rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${YODA_EUS_REF}" "${YODA_EUS_REPO}" /src/yoda-eus && \
    rm -rf /src/yoda-eus/.git

RUN python3 -m venv /opt/yoda/venv && \
    /opt/yoda/venv/bin/pip install --no-cache-dir --upgrade pip gunicorn && \
    /opt/yoda/venv/bin/pip install --no-cache-dir -r /src/yoda-eus/requirements.txt


FROM ubuntu:24.04 AS runtime

LABEL org.opencontainers.image.title="yoda-eus" \
      org.opencontainers.image.vendor="Utrecht University" \
      org.opencontainers.image.description="Production stateless OCI image for Yoda External User Service"

ENV DEBIAN_FRONTEND="noninteractive" \
    PATH="/opt/yoda/venv/bin:${PATH}" \
    YODA_EUS_CONFIG="/etc/yoda/eus.cfg" \
    YODA_EUS_BIND="0.0.0.0:8080"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      libpq5 \
      python3 && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 yoda && \
    useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin yoda && \
    mkdir -p /var/www/extuser /etc/yoda && \
    chown -R yoda:yoda /var/www/extuser /etc/yoda

COPY --from=builder --chown=yoda:yoda /opt/yoda/venv /opt/yoda/venv
COPY --from=builder --chown=yoda:yoda /src/yoda-eus /var/www/extuser
COPY --chmod=0755 entrypoint.sh /usr/local/bin/yoda-eus-entrypoint.sh

USER yoda
WORKDIR /var/www/extuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/ || exit 1

ENTRYPOINT ["/usr/local/bin/yoda-eus-entrypoint.sh"]
