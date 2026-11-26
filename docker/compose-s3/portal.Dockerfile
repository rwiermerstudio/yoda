FROM ghcr.io/utrechtuniversity/yoda-portal:development

COPY roles/yoda_portal/files/s3_research.py /var/www/yoda/research/research.py
COPY roles/yoda_portal/files/s3_research.py /var/www/yoda-copy/research/research.py
RUN chown yodadeployment:yodadeployment /var/www/yoda/research/research.py /var/www/yoda-copy/research/research.py && \
    chmod 0644 /var/www/yoda/research/research.py /var/www/yoda-copy/research/research.py
