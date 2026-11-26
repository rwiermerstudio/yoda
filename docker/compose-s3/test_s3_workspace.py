#!/usr/bin/env python3
"""End-to-end test suite verifying Yoda Workspace + Vault running 100% on S3."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import socket
import subprocess
import sys
import urllib3
import urllib3.util.connection
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_orig_create_connection = urllib3.util.connection.create_connection


def _patched_create_connection(address: tuple[str, int], *args: object, **kwargs: object) -> socket.socket:
    host, port = address
    if host.endswith(".yoda"):
        try:
            socket.gethostbyname(host)
        except socket.gaierror:
            address = ("127.0.0.1", port)
    return _orig_create_connection(address, *args, **kwargs)  # type: ignore[arg-type]


urllib3.util.connection.create_connection = _patched_create_connection

PORTAL_URL = os.environ.get("YODA_PORTAL_URL", "https://portal.yoda:8443")
WEBDAV_URL = os.environ.get("YODA_WEBDAV_URL", "https://data.yoda:8445")
USERNAME = os.environ.get("YODA_TEST_USER", "researcher")
PASSWORD = os.environ.get("YODA_TEST_PASSWORD", "test")
GROUP = os.environ.get("YODA_TEST_GROUP", "research-initial")


def docker_exec_irods(cmd: str) -> str:
    res = subprocess.run(
        ["docker", "exec", "-u", "irods", "provider.yoda", "bash", "-lc", cmd],
        text=True,
        capture_output=True,
        check=True,
    )
    return res.stdout


def extract_csrf(html: str) -> str:
    m = re.search(r'name="csrf_token"\s+type="hidden"\s+value="([^"]+)"', html)
    if not m:
        m = re.search(r"tokenValue:\s*'([^']+)'", html)
    if not m:
        raise RuntimeError("Could not find csrf_token in Portal HTML")
    return m.group(1)


def portal_login(sess: requests.Session, username: str, password: str) -> str:
    r = sess.get(f"{PORTAL_URL}/user/gate", verify=False, timeout=30)
    r.raise_for_status()
    csrf = extract_csrf(r.text)

    r = sess.post(
        f"{PORTAL_URL}/user/gate",
        data={"csrf_token": csrf, "username": username},
        verify=False,
        timeout=30,
    )
    r.raise_for_status()
    csrf = extract_csrf(r.text)

    r = sess.post(
        f"{PORTAL_URL}/user/login",
        data={"csrf_token": csrf, "username": username, "password": password},
        verify=False,
        timeout=30,
    )
    r.raise_for_status()
    r_browse = sess.get(f"{PORTAL_URL}/research/", verify=False, timeout=30)
    r_browse.raise_for_status()
    return extract_csrf(r_browse.text)


def test_1_pure_s3_resources() -> None:
    print("[1/5] Checking iRODS resource tree and verifying zero unixfilesystem resources...")
    out = docker_exec_irods("iquest --no-page '%s|%s' 'SELECT RESC_NAME, RESC_TYPE_NAME'")
    resources = [line.strip().split("|") for line in out.splitlines() if "|" in line]
    # bundleResc is a built-in iRODS pseudo resource that cannot be removed and never stores data
    ufs = [r for r in resources if r[1] == "unixfilesystem" and r[0] != "bundleResc"]
    s3_rescs = [r[0] for r in resources if r[1] == "s3"]
    assert not ufs, f"Expected zero unixfilesystem resources, found: {ufs}"
    assert "s3_workspace_1" in s3_rescs and "s3_vault_1" in s3_rescs, f"Missing S3 resources: {resources}"

    repl_out = docker_exec_irods("iquest --no-page '%s|%s' 'SELECT RESC_NAME, DATA_ID'")
    repl_rescs = {line.split("|")[0].strip() for line in repl_out.splitlines() if "|" in line}
    non_s3 = sorted(repl_rescs - set(s3_rescs))
    assert not non_s3, f"Found data replicas on non-S3 resources: {non_s3}"

    vault_dirs = docker_exec_irods("ls -d /var/lib/irods/Vault* 2>/dev/null || true").strip()
    assert vault_dirs == "", f"Expected zero local /var/lib/irods/Vault* directories, found: {vault_dirs}"
    print(f"      OK: Resources = {resources}; zero local Vault directories.")


def upload_via_portal(
    sess: requests.Session,
    csrf: str,
    filepath: str,
    filename: str,
    payload: bytes,
    chunk_size: int,
) -> None:
    total_size = len(payload)
    total_chunks = max(1, (total_size + chunk_size - 1) // chunk_size)
    flow_id = f"{total_size}-{filename.replace('.', '')}"

    for idx in range(total_chunks):
        chunk_num = idx + 1
        chunk_bytes = payload[idx * chunk_size:(idx + 1) * chunk_size]
        resp = sess.post(
            f"{PORTAL_URL}/research/upload",
            data={
                "csrf_token": csrf,
                "flowChunkNumber": str(chunk_num),
                "flowChunkSize": str(chunk_size),
                "flowCurrentChunkSize": str(len(chunk_bytes)),
                "flowTotalSize": str(total_size),
                "flowIdentifier": flow_id,
                "flowFilename": filename,
                "flowRelativePath": filename,
                "flowTotalChunks": str(total_chunks),
                "filepath": f"/{filepath.strip('/')}",
            },
            files={"file": (filename, io.BytesIO(chunk_bytes), "application/octet-stream")},
            verify=False,
            timeout=120,
        )
        assert resp.status_code == 200, f"Portal chunk {chunk_num}/{total_chunks} failed ({resp.status_code}): {resp.text}"
        assert "Chunk upload succeeded" in resp.text, f"Portal chunk {chunk_num}/{total_chunks} unexpected response: {resp.text[:300]}"


def verify_object_in_irods_and_s3(lpath: str, expected_bytes: bytes) -> None:
    expected_sha256 = hashlib.sha256(expected_bytes).hexdigest()

    coll, name = lpath.rsplit("/", 1)
    resc_info = docker_exec_irods(
        f"iquest --no-page '%s|%s|%s|%s' \"SELECT RESC_NAME, DATA_PATH, DATA_SIZE, DATA_CHECKSUM "
        f"WHERE COLL_NAME = '{coll}' AND DATA_NAME = '{name}'\""
    ).strip()
    rows = [line for line in resc_info.splitlines() if line.count("|") == 3]
    assert len(rows) == 1, f"Expected exactly one replica for {lpath}, got: {resc_info}"
    resc_name, data_path, data_size, data_checksum = rows[0].split("|")
    assert resc_name == "s3_workspace_1", f"Expected replica on s3_workspace_1, got {resc_name}"
    assert int(data_size) == len(expected_bytes), f"iRODS DATA_SIZE {data_size} != {len(expected_bytes)}"
    if data_checksum:
        expected_irods_chksum = "sha2:" + base64.b64encode(bytes.fromhex(expected_sha256)).decode()
        assert data_checksum == expected_irods_chksum, f"iRODS checksum {data_checksum} != {expected_irods_chksum}"

    # Read the object directly from the S3 backend (rodsadmin has no read ACL on research groups)
    s3_key = "/".join(data_path.split("/")[2:])
    s3_info = docker_exec_irods(
        "python3 -c \"import boto3, hashlib; s3=boto3.client('s3', endpoint_url='http://s3.yoda:9000', "
        "aws_access_key_id='s3admin', aws_secret_access_key='s3secret', region_name='local-s3'); "
        f"b=s3.get_object(Bucket='yoda', Key='{s3_key}')['Body'].read(); "
        "print(len(b), hashlib.sha256(b).hexdigest())\""
    ).strip()
    s3_len, s3_sha256 = s3_info.split()
    assert int(s3_len) == len(expected_bytes), f"S3 ContentLength {s3_len} != {len(expected_bytes)}"
    assert s3_sha256 == expected_sha256, f"SHA256 mismatch in S3 for {lpath}: {s3_sha256} != {expected_sha256}"


def test_2_portal_single_and_overwrite(sess: requests.Session, csrf: str) -> None:
    print("[2/5] Testing Portal single-chunk upload and overwrite (regression test for truncate() on S3)...")
    fname = "s3_portal_overwrite_test.txt"
    lpath = f"/tempZone/home/{GROUP}/{fname}"

    v1 = b"Initial S3 workspace version - " + (b"A" * 4096)
    upload_via_portal(sess, csrf, GROUP, fname, v1, chunk_size=25 * 1024 * 1024)
    verify_object_in_irods_and_s3(lpath, v1)

    # Overwrite with a SMALLER file (specifically exercises the path where truncate() previously failed with HTTP 500)
    v2 = b"Overwritten smaller S3 version!"
    upload_via_portal(sess, csrf, GROUP, fname, v2, chunk_size=25 * 1024 * 1024)
    verify_object_in_irods_and_s3(lpath, v2)
    print("      OK: Single-chunk upload + smaller-file overwrite succeeded on S3.")


def test_3_portal_multichunk_streaming(sess: requests.Session, csrf: str) -> None:
    print("[3/5] Testing Portal multi-chunk streaming upload + .part decoupled rename on S3...")
    fname = "s3_multichunk_stream.bin"
    lpath = f"/tempZone/home/{GROUP}/{fname}"
    # 12 MiB split across two 6 MiB chunks (> 5 MiB S3_MPU_CHUNK)
    chunk_sz = 6 * 1024 * 1024
    payload = os.urandom(12 * 1024 * 1024 + 12345)

    upload_via_portal(sess, csrf, GROUP, fname, payload, chunk_size=chunk_sz)
    verify_object_in_irods_and_s3(lpath, payload)

    # Verifyoverwrite of multi-chunk file as well
    payload2 = os.urandom(7 * 1024 * 1024 + 321)
    upload_via_portal(sess, csrf, GROUP, fname, payload2, chunk_size=chunk_sz)
    verify_object_in_irods_and_s3(lpath, payload2)

    # s3_transport leaves empty per-object directories behind; assert no cached file data remains
    cache_files = docker_exec_irods("find /dev/shm/irods-s3-cache -type f").strip()
    assert cache_files == "", f"Expected empty S3_CACHE_DIR after upload, found: {cache_files}"
    print("      OK: Multi-chunk streaming upload & overwrite verified in S3 (S3_CACHE_DIR clean).")


def test_4_webdav_operations() -> None:
    print("[4/5] Testing DavRODS WebDAV PUT / GET / MOVE / DELETE on S3 Workspace...")
    src_url = f"{WEBDAV_URL}/{GROUP}/webdav_s3_test.txt"
    dst_url = f"{WEBDAV_URL}/{GROUP}/webdav_s3_renamed.txt"
    body = b"Hello from DavRODS WebDAV backed purely by S3!\n" * 200

    r_put = requests.put(src_url, data=body, auth=(USERNAME, PASSWORD), verify=False, timeout=60)
    assert r_put.status_code in (200, 201, 204), f"WebDAV PUT failed: {r_put.status_code}"

    r_move = requests.request(
        "MOVE",
        src_url,
        headers={"Destination": f"https://data.yoda/{GROUP}/webdav_s3_renamed.txt", "Overwrite": "T"},
        auth=(USERNAME, PASSWORD),
        verify=False,
        timeout=60,
    )
    assert r_move.status_code in (201, 204), f"WebDAV MOVE failed: {r_move.status_code} {r_move.text}"

    r_get = requests.get(dst_url, auth=(USERNAME, PASSWORD), verify=False, timeout=60)
    assert r_get.status_code == 200 and r_get.content == body, "WebDAV GET content mismatch"
    verify_object_in_irods_and_s3(f"/tempZone/home/{GROUP}/webdav_s3_renamed.txt", body)

    r_del = requests.delete(dst_url, auth=(USERNAME, PASSWORD), verify=False, timeout=60)
    assert r_del.status_code in (200, 204), f"WebDAV DELETE failed: {r_del.status_code}"
    print("      OK: WebDAV PUT, MOVE, GET, and DELETE verified on S3 Workspace.")


def test_5_revisions_on_s3() -> None:
    print("[5/5] Testing Yoda asynchronous file revisions stored on S3...")
    docker_exec_irods(
        "irule -r irods_rule_engine_plugin-irods_rule_language-instance "
        "\"rule_revision_batch('1', '1', '64', '100', '0')\" null ruleExecOut"
    )
    rev_rows = docker_exec_irods(
        f"iquest --no-page '%s|%s' \"SELECT DATA_NAME, RESC_NAME WHERE COLL_NAME like '/tempZone/yoda/revisions/{GROUP}%'\""
    ).strip()
    assert "s3_workspace_1" in rev_rows, f"Expected revisions on s3_workspace_1, got: {rev_rows}"
    print("      OK: Yoda revisions created and stored on s3_workspace_1.")


def main() -> int:
    test_1_pure_s3_resources()
    sess = requests.Session()
    # Flask-WTF enforces a same-origin Referer header for CSRF on HTTPS (WTF_CSRF_SSL_STRICT)
    sess.headers["Referer"] = f"{PORTAL_URL}/"
    csrf = portal_login(sess, USERNAME, PASSWORD)
    test_2_portal_single_and_overwrite(sess, csrf)
    test_3_portal_multichunk_streaming(sess, csrf)
    test_4_webdav_operations()
    test_5_revisions_on_s3()
    print("\nALL S3-ONLY YODA WORKSPACE TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
