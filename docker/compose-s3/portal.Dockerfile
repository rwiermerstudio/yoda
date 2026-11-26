FROM ghcr.io/utrechtuniversity/yoda-portal:development

COPY roles/yoda_portal/files/s3_research.py /var/www/yoda/research/research.py
COPY roles/yoda_portal/files/s3_research.py /var/www/yoda-copy/research/research.py
RUN chown yodadeployment:yodadeployment /var/www/yoda/research/research.py /var/www/yoda-copy/research/research.py && \
    chmod 0644 /var/www/yoda/research/research.py /var/www/yoda-copy/research/research.py

RUN apt-get update && apt-get install -y --no-install-recommends patch && rm -rf /var/lib/apt/lists/*

COPY patches/yoda-portal-perf-connman-ttl.patch /tmp/yoda-portal-perf-connman-ttl.patch
RUN patch -p1 -d /var/www/yoda < /tmp/yoda-portal-perf-connman-ttl.patch && \
    patch -p1 -d /var/www/yoda-copy < /tmp/yoda-portal-perf-connman-ttl.patch && \
    rm -f /tmp/yoda-portal-perf-connman-ttl.patch
