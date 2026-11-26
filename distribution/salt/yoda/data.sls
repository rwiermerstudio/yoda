{% from "yoda/map.jinja" import yoda with context %}

include:
  - yoda.config

yoda_local_inventory:
  file.managed:
    - name: {{ yoda.config_dir }}/hosts
    - mode: '0644'
    - contents: |
        [databases]
        {% if yoda.manage_database %}{{ grains['fqdn'] }} ansible_connection=local{% endif %}

        [icats]
        {% if yoda.manage_irods %}{{ grains['fqdn'] }} ansible_connection=local{% endif %}

        [resources]
    - require:
      - file: yoda_directories

{% if yoda.manage_database %}
yoda_reference_database:
  cmd.run:
    - name: >-
        ansible-playbook -i {{ yoda.config_dir }}/hosts
        {{ yoda.deploy_dir }}/playbooks/reference-infra.yml
        --tags database
    - cwd: {{ yoda.deploy_dir }}
    - creates: /var/lib/yoda/.db_initialized
    - require:
      - file: yoda_local_inventory
      - cmd: yoda_ansible_collections

yoda_mark_db_initialized:
  file.touch:
    - name: /var/lib/yoda/.db_initialized
    - require:
      - cmd: yoda_reference_database
{% endif %}

{% if yoda.manage_irods %}
yoda_irods_platform:
  cmd.run:
    - name: >-
        ansible-playbook -i {{ yoda.config_dir }}/hosts
        {{ yoda.deploy_dir }}/playbooks/irods-platform.yml
    - cwd: {{ yoda.deploy_dir }}
    - unless: systemctl is-active --quiet irods
    - require:
      - file: yoda_local_inventory
      - cmd: yoda_ansible_collections
{% endif %}

{% if yoda.manage_ruleset %}
# Tracks desired ruleset version so bumping yoda:ruleset_version in Pillar triggers upgrade + yoda-migrate
yoda_desired_ruleset_version:
  file.managed:
    - name: {{ yoda.data_dir }}/desired-ruleset-version
    - contents: "{{ yoda.ruleset_version }}\n"
    - mode: '0644'
    - require:
      - file: yoda_directories

yoda_ruleset_deploy:
  cmd.run:
    - name: >-
        ansible-playbook -i {{ yoda.config_dir }}/hosts
        {{ yoda.deploy_dir }}/playbooks/yoda-ruleset.yml
        -e yoda_ruleset_version={{ yoda.ruleset_version }}
        -e yoda_rendered_rules_cfg={{ yoda.rendered_dir }}/icat/rules_uu.cfg
    - cwd: {{ yoda.deploy_dir }}
    - onchanges:
      - file: yoda_desired_ruleset_version
      - cmd: yoda_config_render
    - require:
      - cmd: yoda_config_render
      - file: yoda_local_inventory
{% endif %}
