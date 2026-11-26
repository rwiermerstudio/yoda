# `yoda-config` (Phase 0 Foundation)

Typed, early-fail configuration validator, Ansible environment importer, deterministic renderer, and preflight checker for Yoda (`apiVersion: yoda.uu.nl/v1`).

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 1. Import an existing Ansible environment into yoda.yaml
yoda-config import-ansible ../../environments/development/full -o /tmp/full.yoda.yaml

# 2. Validate yoda.yaml + Release BOM
yoda-config validate /tmp/full.yoda.yaml --bom ../../distribution/release-bom.yaml

# 3. Render component configs (flask.cfg, rules_uu.cfg, web-tier docker-compose.yml)
#    Add --with-reference-edge to also generate a reference Caddy TLS proxy + Anubis/Redis stack
yoda-config render /tmp/full.yoda.yaml --bom ../../distribution/release-bom.yaml --with-reference-edge -o /tmp/rendered

# 4. Diff against currently deployed configs
yoda-config diff /tmp/full.yoda.yaml --bom ../../distribution/release-bom.yaml -t /tmp/rendered

# 5. Export JSON Schema for IDE autocomplete / CI linting
yoda-config schema -o yoda-config.schema.json
```

## Running Tests

```bash
.venv/bin/pytest -v
```
