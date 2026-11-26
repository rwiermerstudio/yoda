---
parent: Administration Tasks
title: Ruleset Extensions & Publication Handoff
nav_order: 3
---
# Ruleset Extensions & Publication Handoff (Phase 2)

## 1. Replacing Inline `git apply` Ruleset Patches

Applying ad-hoc `git apply` patches directly on production iRODS servers makes upgrades fragile and non-repeatable. In the Hybrid (Option C) architecture, institutional customizations follow two supported paths:

### Path A: Extension Directory (`/etc/irods/yoda-extensions.d/`)
Package institutional rules, policies, or metadata hooks as a separate versioned `.deb`/`.rpm` that installs files into `/etc/irods/yoda-extensions.d/<extension-name>/` and declare the extension name in `yoda.yaml`:

```yaml
customizations:
  ruleset_extensions:
    - uu-custom-policies
```

`yoda-config validate` rejects path traversal (`..`, `/`) and keeps core `yoda-ruleset` upgrades completely independent of institutional extensions.

### Path B: Build-Time Patched Package (Transition Escape Hatch)
If an institution still requires a temporary patch against core `yoda-ruleset`, apply it at **CI build time** rather than on production hosts:

```bash
./distribution/packaging/yoda-ruleset/build-package.sh \
  --version 2.2.0+inst1 \
  --ref v2.2.0 \
  --format deb \
  --patch ./patches/institutional.patch
```

This produces a single, signed, rollback-safe package with an isolated Python runtime (`/opt/yoda/ruleset-venv`).

---

## 2. Publication Handoff (`icat` -> Containerized `yoda-public`)

When a data package is published, the iCAT ruleset copies generated landing pages and MOAI metadata XML to the public server (`securecopy.sh`). With a containerized `yoda-public` service:

- **Default (Volume + SFTP/SSH inbox):** Mount persistent host directories `/var/lib/yoda/public/metadata` and `/var/lib/yoda/public/landingpages` into the `yoda-public` container (see `distribution/containers/quadlets/yoda-public.container`). The host's dedicated `inbox` SSH account writes into those directories, keeping `yoda-public` itself stateless and non-root.
- **Object Storage (Optional):** Sync `/var/lib/yoda/public/` via S3-compatible storage when running `yoda-public` across multiple replicas or Kubernetes pods.

---

## 3. Automated Security Patch Rollouts

1. Weekly scheduled runs of `.github/workflows/build-prod-web-images.yml` rebuild all 4 web-tier OCI images, fail on CRITICAL/HIGH CVEs via Trivy, attach SPDX SBOMs, and sign digests with Cosign.
2. Release automation updates immutable digests in `distribution/release-bom.yaml` using:
   ```bash
   yoda-config bump-bom --bom distribution/release-bom.yaml --component portal --digest sha256:...
   ```
3. Operators pull the updated BOM, run `yoda-config diff` + `yoda-config render`, and roll only the affected web container—without touching iRODS or PostgreSQL.
