#!/usr/bin/env python3
"""Migrate Docker Yoda provider from legacy unixfilesystem Vaults to 100% cacheless S3."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

import boto3
from botocore.exceptions import ClientError


S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://s3.yoda:9000")
S3_HOST_PORT = S3_ENDPOINT.replace("http://", "").replace("https://", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "yoda")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "s3admin")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "s3secret")
S3_REGION = os.environ.get("S3_REGION", "local-s3")
S3_CACHE_DIR = "/dev/shm/irods-s3-cache"
S3_AUTH_FILE = "/var/lib/irods/.s3auth"


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    res = subprocess.run(cmd, text=True, capture_output=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"Command {cmd} failed ({res.returncode}): {res.stdout}\n{res.stderr}")
    return res


def wait_for_s3_and_create_bucket() -> None:
    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
    )
    for _ in range(60):
        try:
            s3.list_buckets()
            break
        except Exception:
            time.sleep(1)
    else:
        raise RuntimeError(f"Timed out waiting for S3 endpoint {S3_ENDPOINT}")

    try:
        s3.head_bucket(Bucket=S3_BUCKET)
    except ClientError:
        if S3_REGION and S3_REGION != "us-east-1":
            s3.create_bucket(
                Bucket=S3_BUCKET,
                CreateBucketConfiguration={"LocationConstraint": S3_REGION},
            )
        else:
            s3.create_bucket(Bucket=S3_BUCKET)
    print(f"[s3-init] S3 bucket s3://{S3_BUCKET} ready at {S3_ENDPOINT}")


def ensure_resource(name: str, resc_type: str, host: str = "", path: str = "", context: str = "") -> None:
    chk = run(
        ["iquest", "--no-page", "%s", f"SELECT RESC_NAME WHERE RESC_NAME = '{name}'"],
        check=False,
    )
    if chk.returncode == 0 and name in chk.stdout.split():
        return
    hp = f"{host}:{path}" if (host or path) else ""
    cmd = ["iadmin", "mkresc", name, resc_type]
    if hp:
        cmd.append(hp)
    if context:
        if not hp:
            cmd.append("")
        cmd.append(context)
    run(cmd)


def add_child(parent: str, child: str) -> None:
    run(["iadmin", "addchildtoresc", parent, child], check=False)


def rm_child(parent: str, child: str) -> None:
    run(["iadmin", "rmchildfromresc", parent, child], check=False)


def list_all_data_objects() -> list[tuple[str, str, str]]:
    q = run(
        ["iquest", "--no-page", "%s|%s|%s", "SELECT COLL_NAME, DATA_NAME, RESC_NAME"],
        check=False,
    )
    if q.returncode != 0 or "CAT_NO_ROWS_FOUND" in q.stdout:
        return []
    rows: list[tuple[str, str, str]] = []
    for line in q.stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def list_all_resources() -> list[tuple[str, str, str]]:
    """Return (resc_name, resc_type, parent_id)."""
    q = run(
        ["iquest", "--no-page", "%s|%s|%s", "SELECT RESC_NAME, RESC_TYPE_NAME, RESC_PARENT"],
        check=False,
    )
    rows: list[tuple[str, str, str]] = []
    for line in q.stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def update_rules_cfg() -> None:
    cfg_path = "/etc/irods/yoda-ruleset/rules_uu.cfg"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        content = fh.read()
    replacements = {
        "resource_primary": "'irodsResc'",
        "resource_research": "'irodsResc'",
        "resource_vault": "'irodsRescVaultS3'",
        "resource_replica": "''",
        "arb_enabled": "'false'",
    }
    for key, val in replacements.items():
        content = re.sub(rf"^{key}\s*=.*$", f"{key:<30} = {val}", content, flags=re.MULTILINE)
    with open(cfg_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    print("[s3-init] Updated /etc/irods/yoda-ruleset/rules_uu.cfg for pure S3 Workspace + Vault")


def main() -> int:
    wait_for_s3_and_create_bucket()

    s3_ctx = (
        f"S3_DEFAULT_HOSTNAME={S3_HOST_PORT};"
        f"S3_AUTH_FILE={S3_AUTH_FILE};"
        f"S3_REGIONNAME={S3_REGION};"
        "S3_RETRY_COUNT=3;"
        "S3_WAIT_TIME_SECONDS=2;"
        "S3_PROTO=HTTP;"
        "ARCHIVE_NAMING_POLICY=decoupled;"
        "HOST_MODE=cacheless_attached;"
        f"S3_CACHE_DIR={S3_CACHE_DIR}"
    )

    # 1. Create S3 Workspace & S3 Vault hierarchies (keep s3_workspace detached during migration so irepl -R targets it)
    ensure_resource("s3_workspace_1", "s3", "provider.yoda", f"/{S3_BUCKET}/workspace", s3_ctx)
    ensure_resource("s3_workspace_p1", "passthru")
    ensure_resource("s3_workspace", "random")
    add_child("s3_workspace_p1", "s3_workspace_1")
    add_child("s3_workspace", "s3_workspace_p1")

    ensure_resource("s3_vault_1", "s3", "provider.yoda", f"/{S3_BUCKET}/vault", s3_ctx)
    ensure_resource("s3_vault_p1", "passthru")
    ensure_resource("s3_vault", "random")
    ensure_resource("irodsRescVaultS3", "random")
    add_child("s3_vault_p1", "s3_vault_1")
    add_child("s3_vault", "s3_vault_p1")
    add_child("irodsRescVaultS3", "s3_vault")

    # 2. Replicate every existing preloaded object onto S3 (vault-* -> irodsRescVaultS3, everything else -> s3_workspace)
    all_objs = list_all_data_objects()
    on_s3 = {f"{coll}/{name}" for coll, name, resc in all_objs if resc in ("s3_workspace_1", "s3_vault_1")}
    unique_paths = sorted({f"{coll}/{name}" for coll, name, _ in all_objs} - on_s3)
    print(f"[s3-init] Migrating {len(unique_paths)} preloaded Yoda data objects to S3...")
    for lpath in unique_paths:
        target_resc = "irodsRescVaultS3" if lpath.startswith("/tempZone/home/vault-") else "s3_workspace"
        run(["irepl", "-M", "-R", target_resc, lpath])

    # 3. Detach legacy unixfilesystem subtrees before trimming so replicas can be trimmed cleanly
    rm_child("irodsResc", "dev001")
    rm_child("dev001", "dev001_p1")
    rm_child("dev001", "dev001_p2")
    rm_child("dev001_p1", "dev001_1")
    rm_child("dev001_p2", "dev001_2")
    rm_child("irodsRescRepl", "dev002")
    rm_child("dev002", "dev002_p1")
    rm_child("dev002_p1", "dev002_1")

    # 4. Trim all non-S3 replicas
    for coll, name, resc in list_all_data_objects():
        if resc not in ("s3_workspace_1", "s3_vault_1"):
            lpath = f"{coll}/{name}"
            run(["itrim", "-M", "-N", "1", "-S", resc, lpath])

    # 5. Attach s3_workspace under irodsResc and remove all legacy unixfilesystem/coordinating resources
    add_child("irodsResc", "s3_workspace")
    for legacy_resc in (
        "dev001_1",
        "dev001_2",
        "dev001_p1",
        "dev001_p2",
        "dev001",
        "dev002_1",
        "dev002_p1",
        "dev002",
        "irodsRescRepl",
    ):
        run(["iadmin", "rmresc", legacy_resc], check=False)

    # Verify zero unixfilesystem resources remain (bundleResc is a built-in iRODS pseudo resource that
    # cannot be removed - CAT_PSEUDO_RESC_MODIFY_DISALLOWED - and never stores Yoda data)
    remaining_ufs = [
        name for name, rtype, _ in list_all_resources() if rtype == "unixfilesystem" and name != "bundleResc"
    ]
    if remaining_ufs:
        raise RuntimeError(f"Unexpected unixfilesystem resources still present: {remaining_ufs}")

    # 6. Delete local Vault* directories on filesystem
    for entry in os.listdir("/var/lib/irods"):
        if entry.startswith("Vault"):
            shutil.rmtree(os.path.join("/var/lib/irods", entry), ignore_errors=True)

    update_rules_cfg()
    print("[s3-init] Pure-S3 Yoda Workspace + Vault migration complete!")
    print(run(["ilsresc"]).stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
