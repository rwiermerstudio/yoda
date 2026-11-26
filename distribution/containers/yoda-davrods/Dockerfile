# Production OCI image for yoda-davrods (WebDAV interface to iRODS).
# Runs Apache foreground on internal port 8080 behind external TLS ingress.

FROM ubuntu:24.04 AS runtime

LABEL org.opencontainers.image.title="yoda-davrods" \
      org.opencontainers.image.vendor="Utrecht University" \
      org.opencontainers.image.description="Production stateless OCI image for Yoda DavRODS"

ARG IRODS_VERSION="5.0.2-0~noble"
ARG DAVRODS_RELEASE="5.0.2_1.5.4"
ARG DAVRODS_DEB="davrods-5.0.2-1.5.4.deb"

ENV DEBIAN_FRONTEND="noninteractive" \
    YODA_DAVRODS_VHOST="/etc/yoda/davrods-vhost.conf"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN --mount=type=secret,id=extra_ca,target=/tmp/extra-ca.crt \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      apache2 \
      ca-certificates \
      curl \
      gnupg \
      libjansson4 && \
    curl -fsSL https://packages.irods.org/irods-signing-key.asc | gpg --dearmor -o /usr/share/keyrings/irods.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/irods.gpg] https://packages.irods.org/apt/ noble main" \
      > /etc/apt/sources.list.d/irods.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      "irods-runtime=${IRODS_VERSION}" \
      "irods-icommands=${IRODS_VERSION}" && \
    if [ -s /tmp/extra-ca.crt ]; then \
      cp /tmp/extra-ca.crt /usr/local/share/ca-certificates/extra-ca.crt && update-ca-certificates; \
    fi && \
    curl -fsSL "https://github.com/UtrechtUniversity/davrods/releases/download/${DAVRODS_RELEASE}/${DAVRODS_DEB}" \
      -o "/tmp/${DAVRODS_DEB}" && \
    dpkg -i "/tmp/${DAVRODS_DEB}" && \
    rm -f "/tmp/${DAVRODS_DEB}" && \
    if [ -f /usr/local/share/ca-certificates/extra-ca.crt ]; then \
      rm -f /usr/local/share/ca-certificates/extra-ca.crt && update-ca-certificates --fresh; \
    fi && \
    rm -rf /var/lib/apt/lists/*

# Enable Apache modules required by DavRODS and disable default 000-default site
RUN a2enmod dav dav_fs dav_lock davrods headers && \
    a2dissite 000-default && \
    mkdir -p /etc/yoda /etc/apache2/irods /var/run/apache2 /var/lock/apache2 && \
    chown -R www-data:www-data /var/run/apache2 /var/lock/apache2 /var/log/apache2

COPY --chmod=0755 entrypoint.sh /usr/local/bin/yoda-davrods-entrypoint.sh

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/ -o /dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/yoda-davrods-entrypoint.sh"]
