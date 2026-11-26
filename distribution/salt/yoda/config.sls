{% from "yoda/map.jinja" import yoda with context %}

include:
  - yoda.prereqs

# Manage secrets separately with 0600 permissions (never stored in yoda.yaml)
yoda_secrets_env:
  file.managed:
    - name: {{ yoda.config_dir }}/secrets.env
    - mode: '0600'
    - user: root
    - group: root
    - show_changes: False
    - contents: |
        # Managed by Salt. Do not edit manually.
        {%- for key, value in yoda.secrets.items() %}
        {{ key }}={{ value }}
        {%- endfor %}
    - require:
      - file: yoda_directories

# Single typed configuration source of truth
yoda_yaml_config:
  file.serialize:
    - name: {{ yoda.config_dir }}/yoda.yaml
    - dataset: {{ yoda.config | yaml }}
    - formatter: yaml
    - mode: '0640'
    - user: root
    - group: root
    - require:
      - file: yoda_directories

# Early-fail gate: aborts the entire Salt run before touching any service if invalid
yoda_config_validate:
  cmd.run:
    - name: >-
        {{ yoda.venv_dir }}/bin/yoda-config validate
        {{ yoda.config_dir }}/yoda.yaml
        --bom {{ yoda.deploy_dir }}/distribution/release-bom.yaml
    - require:
      - file: yoda_yaml_config
      - cmd: yoda_config_cli_install

# Deterministically render all component configs + Compose manifests
yoda_config_render:
  cmd.run:
    - name: >-
        {{ yoda.venv_dir }}/bin/yoda-config render
        {{ yoda.config_dir }}/yoda.yaml
        --bom {{ yoda.deploy_dir }}/distribution/release-bom.yaml
        {% if yoda.with_reference_edge %}--with-reference-edge{% endif %}
        -o {{ yoda.rendered_dir }}
    - require:
      - cmd: yoda_config_validate
    - onchanges:
      - file: yoda_yaml_config
      - git: yoda_deploy_repo
