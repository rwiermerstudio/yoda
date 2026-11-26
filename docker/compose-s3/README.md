# Yoda S3-Only Workspace Test Environment

This repository provides **two** ready-to-run test environments where the Yoda Workspace (`research-*`, `deposit-*`, `intake-*`, file revisions, schemas, and templates) and Vault run **100% on S3 object storage** with **zero `unixfilesystem` block-device resources**:

1. **Ansible / Vagrant Environment**: `environments/development/s3_workspace/`
2. **Docker Compose Environment**: `docker/compose-s3/` (backed by [VersityGW](https://github.com/versity/versitygw) S3)

---

## Included Patches

| Component | File / Patch | Purpose |
| --- | --- | --- |
| **Yoda Portal** | `roles/yoda_portal/files/s3_research.py` (`patches/yoda-portal-s3-upload.patch`) | Removes `data_objects.truncate()` (unsupported `RESOURCE_OP_TRUNCATE` on `irods-resource-plugin-s3`, which caused HTTP 500 on overwrite) and streams multi-chunk `Flow.js` uploads over a single cloned `PUT_OPR` handle with `DATA_SIZE_KW` (`use_cache_=false`), eliminating $O(k^2)$ S3 download/re-upload amplification. |
| **Composable Resources** | `roles/composable_resources/` & `library/irods_resource.py` | Skips local directory creation for `s3` resources, fixes boolean evaluation on `modify`, and cleans up the unused bootstrap `/var/lib/irods/Vault` directory when no `unixfilesystem` resources are defined. |
| **S3 Resource Plugin Role** | `roles/irods_resource_plugin_s3/` | Adds `s3_cache_dir` (configured to `/dev/shm/irods-s3-cache` tmpfs in `s3_workspace` with `tmpfiles.d` persistence) so fallback cache operations never touch a block device. |
| **iRODS Checksum Buffer** | `roles/irods_icat/` & `roles/irods_resource/` | Makes `irods_checksum_read_buffer_size_in_bytes` configurable (set to 10 MiB in `s3_workspace` to avoid S3 `GetObject` rate-limiting during async checksum verification). |
| **Consistency Check (`ichk`)** | `roles/irods_consistency_check/` (`patches/irods-consistency-check-s3-key.patch`) | Fixes `S3ResourceInterface._get_key_name` to derive keys from `"/".join(path.split("/")[2:])` instead of hardcoding `"Vault/"`, supporting arbitrary S3 prefixes (`/yoda/workspace`, `/yoda/vault`) and `ARCHIVE_NAMING_POLICY=decoupled`. |
| **DavRODS (Upstream Reference)** | `patches/davrods-s3-content-length.patch` | Reference patch forwarding HTTP `Content-Length` to `open_params->dataSize` (`PUT_OPR`, `numThreads=1`) so single-stream WebDAV `PUT`s bypass `S3_CACHE_DIR`. |

---

## Quick Start: Docker Compose S3 Test Environment

1. Add local hostnames to `/etc/hosts`:
   ```bash
   echo "127.0.0.1 portal.yoda eus.yoda data.yoda public.yoda s3.yoda" | sudo tee -a /etc/hosts
   ```

2. Build and start the stack:
   ```bash
   ./docker/s3-up.sh -d
   ```

3. Wait until migration completes (`docker logs -f provider.yoda` shows `Yoda Workspace and Vault are now running 100% on S3`), then run the end-to-end test suite:
   ```bash
   ./docker/s3-test.sh
   ```

4. Tear down:
   ```bash
   ./docker/s3-down.sh -v
   ```

---

## Quick Start: Ansible / Vagrant Environment (`s3_workspace`)

```bash
vagrant up
ansible-playbook -i environments/development/s3_workspace playbook.yml
ansible-playbook -i environments/development/s3_workspace test.yml
```
