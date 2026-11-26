# Yoda Hybrid (Option C) SaltStack Formula

Installs and upgrades a complete production Yoda instance (PostgreSQL, iRODS, packaged `yoda-ruleset`, containerized web tier, and Caddy TLS + Anubis + Redis edge) with the web and edge tiers managed as `systemd` services (`yoda-web.service` and `yoda-edge.service`).

## Setup

1. Copy `distribution/salt/yoda/` into your Salt file roots (e.g., `/srv/salt/yoda/`).
2. Configure Pillar for your minion using `pillar.example.sls` (encrypt secrets with Salt GPG/Vault/SOPS).
3. Run the initial full install:
   ```bash
   salt 'yoda.example.edu' state.apply yoda
   ```

## Upgrading & Patching

- **Upgrade to a desired release** (e.g., `v2.3.0`):
  Update `yoda:git_ref` and `yoda:ruleset_version` in Pillar, then run:
  ```bash
  salt 'yoda.example.edu' state.apply yoda
  ```
  Salt validates `yoda.yaml` + `release-bom.yaml` first (early-fail), re-renders configs, upgrades `yoda-ruleset` + runs `yoda-migrate`, pre-pulls updated container images, and restarts `yoda-web.service` / `yoda-edge.service`.

- **Web-tier security patch only (~30s, zero iRODS restart):**
  ```bash
  salt 'yoda.example.edu' state.apply yoda.web
  ```

- **Edge / Anubis patch only:**
  ```bash
  salt 'yoda.example.edu' state.apply yoda.edge
  ```

- **Rollback:** Revert `yoda:git_ref` / `yoda:ruleset_version` in Pillar and re-apply `yoda.web` or `yoda`.
