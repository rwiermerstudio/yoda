"""Non-destructive live preflight contract checks against external dependencies."""

from __future__ import annotations

import socket
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .schema import YodaConfig


@dataclass
class PreflightCheckResult:
    name: str
    ok: bool
    detail: str


def _check_tcp(name: str, host: str, port: int, timeout: float = 3.0) -> PreflightCheckResult:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return PreflightCheckResult(name=name, ok=True, detail=f"TCP {host}:{port} reachable")
    except OSError as exc:
        return PreflightCheckResult(name=name, ok=False, detail=f"TCP {host}:{port} failed: {exc}")


def _check_http_get(name: str, url: str, timeout: float = 5.0) -> PreflightCheckResult:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return PreflightCheckResult(name=name, ok=200 <= resp.status < 400, detail=f"HTTP {resp.status} ({url})")
    except Exception as exc:  # noqa: BLE001
        return PreflightCheckResult(name=name, ok=False, detail=f"GET {url} failed: {exc}")


def run_preflight(cfg: YodaConfig, timeout: float = 3.0) -> list[PreflightCheckResult]:
    """Run live, read-only connectivity checks against configured external contracts."""
    results: list[PreflightCheckResult] = []

    # 1. PostgreSQL contract
    results.append(
        _check_tcp("postgresql", cfg.database.host, cfg.database.port, timeout=timeout)
    )

    # 2. iRODS iCAT contract
    results.append(
        _check_tcp("irods_icat", cfg.irods.icat_fqdn, cfg.irods.icat_port, timeout=timeout)
    )

    # 3. SMTP contract
    if cfg.integrations.smtp.notifications_enabled and cfg.integrations.smtp.server:
        parsed = urllib.parse.urlparse(cfg.integrations.smtp.server)
        smtp_host = parsed.hostname or cfg.integrations.smtp.server
        smtp_port = parsed.port or (465 if parsed.scheme == "smtps" else 25)
        results.append(_check_tcp("smtp", smtp_host, smtp_port, timeout=timeout))

    # 4. OIDC JWKS endpoint
    if cfg.auth.oidc.enabled and cfg.auth.oidc.jwks_uri:
        results.append(_check_http_get("oidc_jwks", cfg.auth.oidc.jwks_uri, timeout=timeout))

    # 5. OpenSearch endpoint
    if cfg.modules.opensearch.enabled and cfg.modules.opensearch.server:
        results.append(_check_tcp("opensearch", cfg.modules.opensearch.server, 9200, timeout=timeout))

    return results
