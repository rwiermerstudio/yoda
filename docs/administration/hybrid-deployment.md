---
parent: Administration Tasks
title: Hybrid Production Deployment (Option C)
nav_order: 2
---
# Hybrid Production Deployment (Option C)

This guide covers the modernized, production-oriented deployment architecture for Yoda:

- **Typed, early-fail configuration:** `yoda.yaml` validated and rendered by `yoda-config` (`tools/yoda_config/`).
- **Pinned releases:** `distribution/release-bom.yaml` defines compatible OCI image tags/digests, iRODS/ruleset package versions, and external infrastructure contracts.
- **Stateless Web Tier (OCI Containers):** `yoda-portal`, `yoda-davrods`, `yoda-public`, and `yoda-eus` run as immutable, non-root containers behind your external TLS ingress / reverse proxy.
- **Stateful Data Tier (Packaged VMs):** iRODS servers and `yoda-ruleset` are deployed via version-pinned `.deb`/`.rpm` packages with an isolated Python runtime (`/opt/yoda/ruleset-venv`) and thin layered playbooks (`playbooks/irods-platform.yml`, `playbooks/yoda-ruleset.yml`).
- **External Infrastructure Borders:** PostgreSQL, PgBouncer, OpenSearch, Redis, SMTP, and TLS/WAF are decoupled from Yoda core (an opt-in `playbooks/reference-infra.yml` is provided for small/dev setups).

## 1. Migrate or Create `yoda.yaml`

Convert an existing Ansible environment:
```bash
yoda-config import-ansible environments/production/<instance> -o yoda.yaml
```

Validate schema, security invariants, BOM compatibility, and live external contracts before touching production:
```bash
yoda-config validate yoda.yaml --bom distribution/release-bom.yaml --preflight
```

## 2. Render & Diff Configuration

```bash
yoda-config diff yoda.yaml --bom distribution/release-bom.yaml -t /etc/yoda/rendered
yoda-config render yoda.yaml --bom distribution/release-bom.yaml -o /etc/yoda/rendered

# Optional: also render a turnkey reference edge stack (Caddy TLS proxy + Anubis/Redis)
yoda-config render yoda.yaml --bom distribution/release-bom.yaml --with-reference-edge -o /etc/yoda/rendered
```

## 3. Deploy or Upgrade

### Web Tier (Containers — Compose or Podman Quadlets)
```bash
docker compose -f /etc/yoda/rendered/web-tier/docker-compose.yml pull
docker compose -f /etc/yoda/rendered/web-tier/docker-compose.yml up -d

# Optional reference edge (TLS proxy + Anubis/Redis if enabled in yoda.yaml)
docker compose -f /etc/yoda/rendered/edge/docker-compose.edge.yml up -d
```

For systemd-managed Podman deployments on EL 9 / Ubuntu 24.04 VMs, see `distribution/containers/quadlets/yoda-portal.container`.

### Data Tier (Packaged Ruleset + Automated Migrations)
```bash
# Platform layer (only when upgrading iRODS or changing resource topology)
ansible-playbook -i <inventory> playbooks/irods-platform.yml

# Ruleset application layer (fast package upgrade + automated yoda-migrate)
ansible-playbook -i <inventory> playbooks/yoda-ruleset.yml \
  -e yoda_ruleset_version=2.2.0 \
  -e yoda_rendered_rules_cfg=/etc/yoda/rendered/icat/rules_uu.cfg
```
