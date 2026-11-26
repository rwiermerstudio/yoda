"""Import legacy Ansible group_vars into a typed yoda.yaml specification."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .schema import INSECURE_DEFAULTS, YodaConfig

JINJA_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


@dataclass
class ImportResult:
    config: YodaConfig
    warnings: list[str] = field(default_factory=list)


def _resolve_simple_jinja(value: Any, raw_vars: dict[str, Any]) -> Any:
    """Resolve simple '{{ var_name }}' references found in Ansible group_vars."""
    if isinstance(value, str):
        def _repl(match: re.Match[str]) -> str:
            key = match.group(1)
            if key in raw_vars and not isinstance(raw_vars[key], (dict, list)):
                return str(raw_vars[key])
            return match.group(0)

        return JINJA_VAR_RE.sub(_repl, value)
    if isinstance(value, list):
        return [_resolve_simple_jinja(item, raw_vars) for item in value]
    if isinstance(value, dict):
        return {k: _resolve_simple_jinja(v, raw_vars) for k, v in value.items()}
    return value


def _as_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _load_ansible_group_vars(env_dir: Path) -> dict[str, Any]:
    """Load and merge YAML files from an Ansible environment's group_vars directory."""
    gv_dir = env_dir / "group_vars"
    if not gv_dir.is_dir():
        raise FileNotFoundError(f"Expected 'group_vars' directory under {env_dir}")

    merged: dict[str, Any] = {}
    for yml_file in sorted(gv_dir.iterdir()):
        if yml_file.is_file() and yml_file.suffix in {".yml", ".yaml", ""}:
            content = yaml.safe_load(yml_file.read_text(encoding="utf-8"))
            if isinstance(content, dict):
                merged.update(content)
    return merged


def import_ansible_environment(env_dir: str | Path) -> ImportResult:
    """Convert an Ansible Yoda environment directory into a validated YodaConfig."""
    env_path = Path(env_dir)
    raw = _load_ansible_group_vars(env_path)
    resolved = _resolve_simple_jinja(raw, raw)
    warnings: list[str] = []

    if resolved.get("extra_rulesets"):
        for rs in resolved["extra_rulesets"]:
            if isinstance(rs, dict) and rs.get("patch"):
                warnings.append(
                    f"Deprecated inline ruleset patch detected on '{rs.get('name', 'unknown')}'. "
                    "Inline git-apply patches are deprecated; move to a versioned extension package."
                )

    env_name = resolved.get("yoda_environment", "development")

    def _warn_secret(var_name: str, value: str, env_hint: str) -> str:
        if value in INSECURE_DEFAULTS and env_name != "development":
            warnings.append(
                f"'{var_name}' uses insecure value '{value}'; replace with '{env_hint}' before deploying."
            )
        return value

    # Build resources list
    resources = []
    for res in resolved.get("irods_resources", []) or []:
        if isinstance(res, dict) and "name" in res and "resource_type" in res:
            resources.append(
                {
                    "name": res["name"],
                    "resource_type": res["resource_type"],
                    "host": res.get("host"),
                    "vault_path": res.get("vault_path"),
                    "context": res.get("context"),
                    "children": res.get("children", []),
                }
            )

    # Normalize arb_exempt_resources (can be space-separated string in Ansible)
    arb_exempt_raw = resolved.get("irods_arb_exempt_resources", [])
    if isinstance(arb_exempt_raw, str):
        arb_exempt = [x for x in arb_exempt_raw.split() if x]
    else:
        arb_exempt = list(arb_exempt_raw or [])

    # Normalize irods_repl_resc
    repl_raw = resolved.get("irods_repl_resc", [])
    if isinstance(repl_raw, str):
        repl_resc = [repl_raw] if repl_raw else []
    else:
        repl_resc = list(repl_raw or [])

    eus_fqdn = resolved.get("yoda_eus_fqdn")
    eus_enabled = bool(eus_fqdn)

    data: dict[str, Any] = {
        "apiVersion": "yoda.uu.nl/v1",
        "kind": "YodaInstanceConfig",
        "instance": {
            "name": str(resolved.get("instance", env_path.name)),
            "environment": env_name,
            "yoda_version": str(resolved.get("yoda_version", "2.2.0-dev")),
            "yoda_prefix": str(resolved.get("yoda_prefix", "UU01")),
            "insecure_config_check": resolved.get("common_insecure_config_check", "auto"),
        },
        "endpoints": {
            "portal_fqdn": resolved.get("yoda_portal_fqdn", "portal.yoda.test"),
            "davrods_fqdn": resolved.get("yoda_davrods_fqdn", "data.yoda.test"),
            "davrods_anonymous_enabled": _as_bool(resolved.get("yoda_davrods_anonymous_enabled"), False),
            "davrods_anonymous_fqdn": resolved.get("yoda_davrods_anonymous_fqdn"),
            "public_fqdn": resolved.get("yoda_public_fqdn", "public.yoda.test"),
            "eus_fqdn": eus_fqdn,
            "eus_api_fqdn": resolved.get("eus_api_fqdn", eus_fqdn),
            "eus_api_port": int(resolved.get("eus_api_port", 8443)),
            "eus_api_tls_verify": _as_bool(resolved.get("eus_api_tls_verify"), env_name != "development"),
        },
        "database": {
            "host": resolved.get("irods_database_fqdn", "database.yoda.test"),
            "port": int(resolved.get("irods_database_port", 5432)),
            "name": resolved.get("irods_database_name", "ICAT"),
            "user": resolved.get("irods_database_user", "irods"),
            "password": _warn_secret(
                "irods_database_password",
                str(resolved.get("irods_database_password", "irodsdev")),
                "env://YODA_DB_PASSWORD",
            ),
            "ssl_mode": "disable" if env_name == "development" else "require",
            "enable_yoda_indexes": _as_bool(resolved.get("irods_database_enable_yoda_indexes"), False),
        },
        "irods": {
            "zone": resolved.get("irods_zone", "tempZone"),
            "icat_fqdn": resolved.get("irods_icat_fqdn", "icat.yoda.test"),
            "icat_port": int(resolved.get("irods_icat_port", 1247)),
            "admin_user": resolved.get("irods_admin", "rods"),
            "admin_password": _warn_secret(
                "irods_password",
                str(resolved.get("irods_password", "rods")),
                "env://YODA_IRODS_ADMIN_PASSWORD",
            ),
            "authentication_scheme": resolved.get("irods_authentication_scheme", "pam_password"),
            "ssl_verify_server": resolved.get(
                "irods_ssl_verify_server", "none" if env_name == "development" else "cert"
            ),
            "ca_certificate_file": resolved.get(
                "irods_ca_certificate_file", "/etc/irods/localhost_and_chain.crt"
            ),
            "default_resource": resolved.get("irods_default_resc", "irodsResc"),
            "replica_resources": repl_resc,
            "research_resource": resolved.get("resource_research", resolved.get("irods_default_resc", "irodsResc")),
            "vault_resource": resolved.get("resource_vault", resolved.get("irods_default_resc", "irodsResc")),
            "trigger_policy_resources": list(resolved.get("irods_resc_trigger_pol", []) or []),
            "replication_exempt_resources": list(resolved.get("irods_resc_repl_exempt", []) or []),
            "enable_nfs_resource": _as_bool(resolved.get("enable_nfs_resource"), False),
            "enable_s3_resource": _as_bool(resolved.get("enable_s3_resource"), False),
            "anonymous_permit_addresses": list(
                resolved.get("irods_anonymous_account_permit_addresses", []) or []
            ),
            "user_max_connections_enabled": _as_bool(
                resolved.get("irods_user_max_connections_enabled"), False
            ),
            "user_max_connections_number": int(resolved.get("irods_user_max_connections_number", 4)),
            "arb_enabled": _as_bool(resolved.get("irods_arb_enabled"), False),
            "arb_exempt_resources": arb_exempt,
            "arb_min_gb_free": int(resolved.get("irods_arb_min_gb_free", 0)),
            "arb_min_percent_free": int(resolved.get("irods_arb_min_percent_free", 5)),
            "resources": resources,
        },
        "portal": {
            "title": resolved.get("portal_title_text", "Yoda"),
            "secret_key": "env://YODA_FLASK_SECRET_KEY",
            "session_cookie_samesite": resolved.get(
                "portal_session_cookie_samesite",
                "Lax" if _as_bool(resolved.get("oidc_active"), False) else "Strict",
            ),
            "permanent_session_lifetime": int(resolved.get("flask_permanent_session_lifetime", 1800)),
            "caching_enabled": _as_bool(resolved.get("enable_portal_caching"), False),
            "log_api_call_duration": _as_bool(resolved.get("yoda_portal_log_api_call_duration"), False),
            "upload_part_files": _as_bool(resolved.get("yoda_portal_upload_part_files"), True),
            "text_file_extensions": list(
                resolved.get("text_file_extensions", ["txt", "md", "csv", "json", "yaml", "xml"])
            ),
        },
        "auth": {
            "oidc": {
                "enabled": _as_bool(resolved.get("oidc_active"), False),
                "domains": list(resolved.get("oidc_domains", []) or []),
                "always_redirect": _as_bool(resolved.get("oidc_always_redirect"), False),
                "client_id": str(resolved.get("oidc_client_id", "")),
                "client_secret": str(resolved.get("oidc_client_secret", "")),
                "auth_base_uri": str(resolved.get("oidc_auth_base_uri", "")),
                "login_hint": _as_bool(resolved.get("oidc_login_hint"), True),
                "token_uri": str(resolved.get("oidc_token_uri", "")),
                "userinfo_uri": str(resolved.get("oidc_userinfo_uri", "")),
                "scopes": str(resolved.get("oidc_scopes", "openid")),
                "acr_values": str(resolved.get("oidc_acr_values", "")),
                "email_field": str(resolved.get("oidc_email_field", "email")),
                "jwks_uri": str(resolved.get("oidc_jwks_uri", "")),
                "jwt_issuer": str(resolved.get("oidc_jwt_issuer", "")),
            },
            "sram": {
                "enabled": _as_bool(resolved.get("enable_sram"), False),
                "rest_api_url": str(resolved.get("sram_rest_api_url", "")),
                "api_key": str(resolved.get("sram_api_key", "")),
                "service_entity_id": str(resolved.get("sram_service_entity_id", "")),
                "verbose_logging": _as_bool(resolved.get("sram_verbose_logging"), False),
                "tls_verify": _as_bool(resolved.get("sram_tls_verify"), env_name != "development"),
                "co_default_label": str(resolved.get("sram_co_default_label", "Yoda")),
                "co_logo": str(resolved.get("sram_co_logo", "")),
                "co_default_admins": list(resolved.get("sram_co_default_admins", []) or []),
                "external_users_co": str(resolved.get("sram_external_users_co", "")),
            },
            "external_users_enabled": _as_bool(resolved.get("external_users"), True),
            "external_users_domain_filter": str(resolved.get("external_users_domain_filter", "*")),
        },
        "modules": {
            "research_enabled": _as_bool(resolved.get("enable_research"), True),
            "deposit_enabled": _as_bool(resolved.get("enable_deposit"), False),
            "data_package_reference_enabled": _as_bool(
                resolved.get("enable_data_package_reference"),
                _as_bool(resolved.get("enable_deposit"), False),
            ),
            "datarequest": {
                "enabled": _as_bool(resolved.get("enable_datarequest"), False),
                "help_contact_name": str(resolved.get("datarequest_help_contact_name", "")),
                "help_contact_email": str(resolved.get("datarequest_help_contact_email", "")),
            },
            "opensearch": {
                "enabled": _as_bool(resolved.get("enable_open_search"), False),
                "server": str(resolved.get("opensearch_server", "")),
            },
            "tokens": {
                "enabled": _as_bool(resolved.get("enable_tokens"), True),
                "database_path": str(
                    resolved.get("token_database", "/etc/irods/yoda-ruleset/accesstokens.db")
                ),
                "database_password": _warn_secret(
                    "token_database_password",
                    str(resolved.get("token_database_password", "env://YODA_TOKEN_DB_PASSWORD")),
                    "env://YODA_TOKEN_DB_PASSWORD",
                ),
                "length": int(resolved.get("token_length", 32)),
                "lifetime_hours": int(resolved.get("token_lifetime", 72)),
                "expiration_notification_hours": int(
                    resolved.get("token_expiration_notification", 24)
                ),
            },
            "archive": {
                "enabled": _as_bool(resolved.get("enable_data_package_archive"), False),
                "fqdn": str(resolved.get("data_package_archive_fqdn", "")),
                "minimum_bytes": int(resolved.get("data_package_archive_minimum", 1073741824)),
                "maximum_bytes": int(resolved.get("data_package_archive_maximum", -1)),
                "resource": str(resolved.get("data_package_archive_resource", "")),
            },
            "clienttools_enabled": _as_bool(resolved.get("enable_yoda_clienttools"), False),
            "report_enabled": _as_bool(resolved.get("enable_yoda_report"), False),
            "async_checksum_enabled": _as_bool(resolved.get("enable_async_checksum"), True),
            "async_checksum_delay_time": int(resolved.get("async_checksum_delay_time", 0)),
            "vault_copy_multithread_enabled": _as_bool(
                resolved.get("yoda_rulesets_vault_copy_multithread_enabled"), False
            ),
            "inactivity_notification_enabled": _as_bool(
                resolved.get("enable_inactivity_notification"), False
            ),
            "inactivity_cutoff_months": int(resolved.get("inactivity_cutoff_months", 6)),
            "measure_coverage": _as_bool(resolved.get("yoda_rulesets_measure_coverage"), False),
        },
        "integrations": {
            "smtp": {
                "notifications_enabled": _as_bool(resolved.get("send_notifications"), False),
                "sender_email": str(resolved.get("notifications_sender_email", "")),
                "sender_name": str(resolved.get("notifications_sender_name", "Yoda")),
                "reply_to": str(resolved.get("notifications_reply_to", "")),
                "server": str(resolved.get("smtp_server", "smtp://localhost:25")),
                "username": str(resolved.get("smtp_username", "")),
                "password": str(resolved.get("smtp_password", "")),
                "auth": _as_bool(resolved.get("smtp_auth"), True),
                "starttls": _as_bool(resolved.get("smtp_starttls"), True),
            },
            "datacite": {
                "rest_api_url": str(resolved.get("datacite_rest_api_url", "api.test.datacite.org")),
                "username": str(resolved.get("datacite_username", "")),
                "password": str(resolved.get("datacite_password", "")),
                "publisher": str(resolved.get("datacite_publisher", "Utrecht University")),
                "tls_verify": _as_bool(resolved.get("datacite_tls_verify"), env_name != "development"),
            },
            "epic": {
                "enabled": _as_bool(resolved.get("epic_pid_enabled"), False),
                "url": str(resolved.get("epic_url", "")),
                "handle_prefix": str(resolved.get("epic_handle_prefix", "")),
            },
            "eus": {
                "enabled": eus_enabled,
                "api_secret": _warn_secret(
                    "eus_api_secret",
                    str(resolved.get("eus_api_secret", "env://YODA_EUS_API_SECRET")),
                    "env://YODA_EUS_API_SECRET",
                ),
                "db_password": _warn_secret(
                    "eus_db_password",
                    str(resolved.get("eus_db_password", "env://YODA_EUS_DB_PASSWORD")),
                    "env://YODA_EUS_DB_PASSWORD",
                ),
            },
            "matomo": {
                "tracking_enabled": _as_bool(resolved.get("yoda_matomo_tracking_enabled"), False),
                "counter_enabled": _as_bool(resolved.get("yoda_matomo_counter_enabled"), False),
                "server_fqdn": str(resolved.get("yoda_matomo_server_fqdn", "")),
                "site_id": str(resolved.get("yoda_matomo_site_id", "1")),
            },
            "anubis": {
                "enabled": _as_bool(resolved.get("enable_anubis"), False),
                "redis_password": str(resolved.get("anubis_redis_password", "")),
                "ed25519_private_key": str(resolved.get("anubis_ed25519_private_key", "")),
            },
        },
        "customizations": {
            "default_schema": str(resolved.get("default_yoda_schema", "default-3")),
            "schemas": list(resolved.get("metadata_schemas", ["default-3"]) or ["default-3"]),
            "theme": str(resolved.get("yoda_theme", "uu")),
            "theme_path": str(resolved.get("yoda_theme_path", "/var/www/yoda/themes")),
            "theme_mapping": dict(resolved.get("yoda_theme_mapping", {}) or {}),
        },
    }

    if _as_bool(resolved.get("enable_postfix"), False):
        warnings.append(
            "enable_postfix is set: in the hybrid architecture, local MTA/Postfix is an external "
            "infrastructure concern (see reference-infra/)."
        )
    if _as_bool(resolved.get("enable_pgbouncer"), False):
        warnings.append(
            "enable_pgbouncer is set: point database.host/port at your external PgBouncer endpoint."
        )

    config = YodaConfig.model_validate(data)
    return ImportResult(config=config, warnings=warnings)
