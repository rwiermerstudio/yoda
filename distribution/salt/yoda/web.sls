{% from "yoda/map.jinja" import yoda with context %}

include:
  - yoda.config

yoda_web_systemd_unit:
  file.managed:
    - name: /etc/systemd/system/yoda-web.service
    - source: salt://yoda/files/yoda-web.service.j2
    - template: jinja
    - mode: '0644'
    - context:
        yoda: {{ yoda | json }}

yoda_web_systemd_reload:
  cmd.run:
    - name: systemctl daemon-reload
    - onchanges:
      - file: yoda_web_systemd_unit

# Pre-pull updated BOM images before restarting systemd service (minimizes downtime)
yoda_web_pull_images:
  cmd.run:
    - name: >-
        {{ yoda.compose_cmd }}
        --env-file {{ yoda.config_dir }}/secrets.env
        -f {{ yoda.rendered_dir }}/web-tier/docker-compose.yml
        pull
    - onchanges:
      - cmd: yoda_config_render
    - require:
      - cmd: yoda_config_render
      - file: yoda_secrets_env

yoda_web_service:
  service.running:
    - name: yoda-web
    - enable: True
    - require:
      - file: yoda_web_systemd_unit
      - cmd: yoda_web_systemd_reload
      - file: yoda_secrets_env
      - cmd: yoda_config_validate
    - watch:
      - file: yoda_web_systemd_unit
      - file: yoda_secrets_env
      - cmd: yoda_config_render
      - cmd: yoda_web_pull_images
