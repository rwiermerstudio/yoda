FROM ghcr.io/utrechtuniversity/yoda-provider:development

ENV DEBIAN_FRONTEND="noninteractive"
ENV IRODS_S3_PLUGIN_VERSION="5.0.1-0+5.0.2~noble"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        "irods-resource-plugin-s3=${IRODS_S3_PLUGIN_VERSION}" \
        python3-boto3 \
        patch && \
    rm -rf /var/lib/apt/lists/*

# Tune checksum_read_buffer_size_in_bytes to 10 MiB as recommended for S3 resources
RUN python3 -c "import json; p='/etc/irods/server_config.json'; cfg=json.load(open(p)); cfg['advanced_settings']['checksum_read_buffer_size_in_bytes']=10485760; json.dump(cfg, open(p,'w'), indent=2)" && \
    chown irods:irods /etc/irods/server_config.json

COPY docker/compose-s3/migrate_to_s3.py /var/lib/irods/scripts/migrate_to_s3.py
COPY docker/compose-s3/s3-provider-init.sh /var/lib/irods/scripts/s3-provider-init.sh
RUN chmod 0755 /var/lib/irods/scripts/migrate_to_s3.py /var/lib/irods/scripts/s3-provider-init.sh && \
    chown irods:irods /var/lib/irods/scripts/migrate_to_s3.py


COPY patches/yoda-ruleset-perf-revision-fastpath.patch /tmp/yoda-ruleset-perf-revision-fastpath.patch
RUN patch -p1 -d /etc/irods/yoda-ruleset < /tmp/yoda-ruleset-perf-revision-fastpath.patch && rm -f /tmp/yoda-ruleset-perf-revision-fastpath.patch

CMD ["/bin/bash", "-c", "exec /var/lib/irods/scripts/s3-provider-init.sh \"${TAG}\""]
