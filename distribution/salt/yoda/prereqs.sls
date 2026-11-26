{% from "yoda/map.jinja" import yoda with context %}

yoda_base_packages:
  pkg.installed:
    - pkgs:
      - git
      - python3
      - python3-venv
      - curl

yoda_directories:
  file.directory:
    - names:
      - {{ yoda.config_dir }}
      - {{ yoda.config_dir }}/themes
      - {{ yoda.rendered_dir }}
      - {{ yoda.data_dir }}/public/metadata
      - {{ yoda.data_dir }}/public/landingpages
    - makedirs: True
    - mode: '0755'

# Pin deploy repo to desired release tag/branch from Pillar (yoda:git_ref).
# Bumping yoda:git_ref in Pillar pulls the new Release BOM & playbooks cleanly.
yoda_deploy_repo:
  git.latest:
    - name: {{ yoda.repo_url }}
    - target: {{ yoda.deploy_dir }}
    - rev: {{ yoda.git_ref }}
    - branch: {{ yoda.git_ref }}
    - force_fetch: True
    - force_reset: True
    - require:
      - pkg: yoda_base_packages

yoda_config_venv:
  cmd.run:
    - name: python3 -m venv {{ yoda.venv_dir }}
    - creates: {{ yoda.venv_dir }}/bin/pip
    - require:
      - pkg: yoda_base_packages

yoda_config_cli_install:
  cmd.run:
    - name: {{ yoda.venv_dir }}/bin/pip install --quiet -e {{ yoda.deploy_dir }}/tools/yoda_config
    - onchanges:
      - git: yoda_deploy_repo
      - cmd: yoda_config_venv
    - require:
      - cmd: yoda_config_venv
      - git: yoda_deploy_repo

yoda_ansible_collections:
  cmd.run:
    - name: ansible-galaxy collection install -r {{ yoda.deploy_dir }}/requirements.yml
    - onchanges:
      - git: yoda_deploy_repo
    - require:
      - git: yoda_deploy_repo
