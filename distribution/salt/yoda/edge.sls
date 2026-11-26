{% from "yoda/map.jinja" import yoda with context %}

include:
  - yoda.web

{% if yoda.with_reference_edge %}
yoda_edge_systemd_unit:
  file.managed:
    - name: /etc/systemd/system/yoda-edge.service
    - source: salt://yoda/files/yoda-edge.service.j2
    - template: jinja
    - mode: '0644'
    - context:
        yoda: {{ yoda | json }}

yoda_edge_systemd_reload:
  cmd.run:
    - name: systemctl daemon-reload
    - onchanges:
      - file: yoda_edge_systemd_unit

yoda_edge_pull_images:
  cmd.run:
    - name: >-
        {{ yoda.compose_cmd }}
        --env-file {{ yoda.config_dir }}/secrets.env
        -f {{ yoda.rendered_dir }}/edge/docker-compose.edge.yml
        pull
    - onchanges:
      - cmd: yoda_config_render
    - require:
      - cmd: yoda_config_render
      - file: yoda_secrets_env

yoda_edge_service:
  service.running:
    - name: yoda-edge
    - enable: True
    - require:
      - service: yoda_web_service
      - file: yoda_edge_systemd_unit
      - cmd: yoda_edge_systemd_reload
    - watch:
      - file: yoda_edge_systemd_unit
      - file: yoda_secrets_env
      - cmd: yoda_config_render
      - cmd: yoda_edge_pull_images
{% else %}
yoda_edge_service_disabled:
  service.dead:
    - name: yoda-edge
    - enable: False
{% endif %}
