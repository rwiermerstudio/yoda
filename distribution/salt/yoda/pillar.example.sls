# Example Salt Pillar for a single-instance production Yoda stack.
# Store secrets via Salt GPG/Vault/SOPS pillar encryption in production.
#
# To upgrade to a newer release or security patch:
#   1. Update `yoda:git_ref` (e.g. v2.3.0) and `yoda:ruleset_version` (e.g. 2.3.0)
#   2. Run `salt 'yoda.example.edu' state.apply yoda` (or `yoda.web` for web-tier CVE patches only)

yoda:
  repo_url: https://github.com/rwiermerstudio/yoda.git
  git_ref: feat/hybrid-distribution-modernization
  ruleset_version: "2.2.0"
  manage_database: true
  manage_irods: true
  manage_ruleset: true
  with_reference_edge: true
  compose_cmd: docker compose

  secrets:
    YODA_DB_PASSWORD: "replace-with-strong-db-password"
    YODA_IRODS_ADMIN_PASSWORD: "replace-with-strong-irods-password"
    YODA_FLASK_SECRET_KEY: "replace-with-random-base64-secret"
    YODA_TOKEN_DB_PASSWORD: "replace-with-strong-token-db-password"
    YODA_EUS_API_SECRET: "replace-with-strong-eus-api-secret"
    YODA_EUS_DB_PASSWORD: "replace-with-strong-eus-db-password"
    YODA_SMTP_PASSWORD: "replace-with-smtp-password"
    YODA_DATACITE_PASSWORD: "replace-with-datacite-password"
    ANUBIS_REDIS_PASSWORD: "replace-with-strong-redis-password"
    ANUBIS_ED25519_PRIVATE_KEY: "replace-with-64-hex-char-ed25519-key"

  config:
    apiVersion: yoda.uu.nl/v1
    kind: YodaInstanceConfig
    instance:
      name: prod-single
      environment: production
      yoda_version: "2.2.0"
      yoda_prefix: EX01
    endpoints:
      portal_fqdn: portal.yoda.example.edu
      davrods_fqdn: data.yoda.example.edu
      davrods_anonymous_enabled: true
      davrods_anonymous_fqdn: public.data.yoda.example.edu
      public_fqdn: public.yoda.example.edu
      eus_fqdn: eus.yoda.example.edu
      eus_api_fqdn: eus.yoda.example.edu
      eus_api_port: 8443
      eus_api_tls_verify: true
    database:
      host: 127.0.0.1
      port: 5432
      name: ICAT
      user: irods
      password: env://YODA_DB_PASSWORD
      ssl_mode: require
      enable_yoda_indexes: true
    irods:
      zone: exampleZone
      icat_fqdn: yoda.example.edu
      icat_port: 1247
      admin_user: rods
      admin_password: env://YODA_IRODS_ADMIN_PASSWORD
      authentication_scheme: pam_password
      ssl_verify_server: cert
      ca_certificate_file: /etc/irods/localhost_and_chain.crt
      default_resource: resc01
      research_resource: resc01
      vault_resource: resc01
      resources:
        - name: resc01
          resource_type: unixfilesystem
          host: yoda.example.edu
          vault_path: /var/lib/irods/Vault
    portal:
      title: "Example University Yoda"
      secret_key: env://YODA_FLASK_SECRET_KEY
      session_cookie_samesite: Strict
    modules:
      research_enabled: true
      deposit_enabled: true
      data_package_reference_enabled: true
      tokens:
        enabled: true
        database_password: env://YODA_TOKEN_DB_PASSWORD
        lifetime_hours: 72
    integrations:
      smtp:
        notifications_enabled: true
        sender_email: yoda@example.edu
        sender_name: Yoda
        reply_to: rdm-support@example.edu
        server: smtp://mail.example.edu:587
        username: yoda
        password: env://YODA_SMTP_PASSWORD
        auth: true
        starttls: true
      datacite:
        rest_api_url: api.datacite.org
        username: EXAMPLE.YODA
        password: env://YODA_DATACITE_PASSWORD
        publisher: "Example University"
        tls_verify: true
      eus:
        enabled: true
        api_secret: env://YODA_EUS_API_SECRET
        db_password: env://YODA_EUS_DB_PASSWORD
      anubis:
        enabled: true
        redis_password: env://ANUBIS_REDIS_PASSWORD
        ed25519_private_key: env://ANUBIS_ED25519_PRIVATE_KEY
    customizations:
      default_schema: default-3
      schemas: ["default-3"]
      theme: uu
      theme_path: /etc/yoda/themes
