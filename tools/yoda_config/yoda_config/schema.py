"""Strongly-typed, early-fail configuration schema for Yoda (apiVersion: yoda.uu.nl/v1)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

INSECURE_DEFAULTS = {
    "PLACEHOLDER",
    "rods",
    "irodsdev",
    "test",
    "yodadev",
    "6df19fb7373c1582789d279218147adce2422b80c0fd3389f52d390490e338e9",
}

EnvironmentType = Literal["development", "testing", "acceptance", "production"]


class StrictModel(BaseModel):
    """Base model that rejects unknown keys so config typos fail early."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def is_secret_reference(value: str) -> bool:
    """Return True if value uses a secret reference scheme (env:// or file://)."""
    return value.startswith("env://") or value.startswith("file://")


def resolve_secret(value: str) -> str:
    """Resolve an env:// or file:// secret reference, or return literal value."""
    if value.startswith("env://"):
        var_name = value[len("env://") :]
        if var_name not in os.environ:
            raise ValueError(f"Secret environment variable '{var_name}' is not set.")
        return os.environ[var_name]
    if value.startswith("file://"):
        secret_path = Path(value[len("file://") :])
        if not secret_path.is_file():
            raise ValueError(f"Secret file '{secret_path}' does not exist.")
        return secret_path.read_text(encoding="utf-8").strip()
    return value


class InstanceConfig(StrictModel):
    name: str = Field(..., min_length=1, pattern=r"^[a-zA-Z0-9._-]+$")
    environment: EnvironmentType = "production"
    yoda_version: str = Field("2.2.0-dev", min_length=1)
    yoda_prefix: str = Field("UU01", min_length=1, max_length=16)
    insecure_config_check: Literal["auto", "enabled", "disabled"] = "auto"

    @property
    def enforce_security_checks(self) -> bool:
        if self.insecure_config_check == "enabled":
            return True
        if self.insecure_config_check == "disabled":
            return False
        return self.environment != "development"


class EndpointsConfig(StrictModel):
    portal_fqdn: str = Field(..., min_length=1)
    davrods_fqdn: str = Field(..., min_length=1)
    davrods_anonymous_enabled: bool = False
    davrods_anonymous_fqdn: str | None = None
    public_fqdn: str = Field(..., min_length=1)
    eus_fqdn: str | None = None
    eus_api_fqdn: str | None = None
    eus_api_port: int = Field(8443, ge=1, le=65535)
    eus_api_tls_verify: bool = True

    @model_validator(mode="after")
    def validate_endpoints(self) -> EndpointsConfig:
        if self.davrods_anonymous_enabled and not self.davrods_anonymous_fqdn:
            raise ValueError(
                "endpoints.davrods_anonymous_fqdn is required when davrods_anonymous_enabled is true."
            )
        return self


class DatabaseContractConfig(StrictModel):
    """External PostgreSQL contract (BYO in production)."""

    host: str = Field(..., min_length=1)
    port: int = Field(5432, ge=1, le=65535)
    name: str = "ICAT"
    user: str = "irods"
    password: str = Field(..., min_length=1)
    ssl_mode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = (
        "require"
    )
    enable_yoda_indexes: bool = False


class IrodsResourceDef(StrictModel):
    name: str = Field(..., min_length=1)
    resource_type: Literal[
        "unixfilesystem", "passthru", "random", "replication", "s3", "compound", "deferred"
    ]
    host: str | None = None
    vault_path: str | None = None
    context: str | None = None
    children: list[str] = Field(default_factory=list)


class IrodsConfig(StrictModel):
    zone: str = Field(..., min_length=1)
    icat_fqdn: str = Field(..., min_length=1)
    icat_port: int = Field(1247, ge=1, le=65535)
    admin_user: str = "rods"
    admin_password: str = Field(..., min_length=1)
    authentication_scheme: Literal["pam_password", "native"] = "pam_password"
    ssl_verify_server: Literal["cert", "hostname", "none"] = "cert"
    ca_certificate_file: str = "/etc/irods/localhost_and_chain.crt"
    default_resource: str = Field(..., min_length=1)
    replica_resources: list[str] = Field(default_factory=list)
    research_resource: str = "irodsResc"
    vault_resource: str = "irodsResc"
    trigger_policy_resources: list[str] = Field(default_factory=list)
    replication_exempt_resources: list[str] = Field(default_factory=list)
    enable_nfs_resource: bool = False
    enable_s3_resource: bool = False
    anonymous_permit_addresses: list[str] = Field(default_factory=list)
    user_max_connections_enabled: bool = False
    user_max_connections_number: int = Field(4, ge=1)
    arb_enabled: bool = False
    arb_exempt_resources: list[str] = Field(default_factory=list)
    arb_min_gb_free: int = Field(0, ge=0)
    arb_min_percent_free: int = Field(5, ge=0, le=100)
    resources: list[IrodsResourceDef] = Field(default_factory=list)


class OidcConfig(StrictModel):
    enabled: bool = False
    domains: list[str] = Field(default_factory=list)
    always_redirect: bool = False
    client_id: str = ""
    client_secret: str = ""
    auth_base_uri: str = ""
    login_hint: bool = True
    token_uri: str = ""
    userinfo_uri: str = ""
    scopes: str = "openid"
    acr_values: str = ""
    email_field: str = "email"
    jwks_uri: str = ""
    jwt_issuer: str = ""
    req_exp: bool = True
    req_iat: bool = False
    req_nbf: bool = False
    verify_aud: bool = True
    verify_iat: bool = False
    verify_exp: bool = True
    verify_iss: bool = True

    @model_validator(mode="after")
    def validate_oidc(self) -> OidcConfig:
        if self.enabled:
            required = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_base_uri": self.auth_base_uri,
                "token_uri": self.token_uri,
                "jwks_uri": self.jwks_uri,
                "jwt_issuer": self.jwt_issuer,
            }
            missing = [k for k, v in required.items() if not v]
            if missing:
                raise ValueError(
                    f"auth.oidc is enabled but missing required fields: {', '.join(missing)}"
                )
        return self


class SramConfig(StrictModel):
    enabled: bool = False
    rest_api_url: str = ""
    api_key: str = ""
    service_entity_id: str = ""
    verbose_logging: bool = False
    tls_verify: bool = True
    co_default_label: str = "Yoda"
    co_logo: str = ""
    co_default_admins: list[str] = Field(default_factory=list)
    external_users_co: str = ""
    oidc: OidcConfig = Field(default_factory=OidcConfig)

    @model_validator(mode="after")
    def validate_sram(self) -> SramConfig:
        if self.enabled:
            missing = [
                k
                for k, v in {
                    "rest_api_url": self.rest_api_url,
                    "api_key": self.api_key,
                    "service_entity_id": self.service_entity_id,
                }.items()
                if not v
            ]
            if missing:
                raise ValueError(
                    f"auth.sram is enabled but missing required fields: {', '.join(missing)}"
                )
        return self


class AuthConfig(StrictModel):
    oidc: OidcConfig = Field(default_factory=OidcConfig)
    sram: SramConfig = Field(default_factory=SramConfig)
    external_users_enabled: bool = True
    external_users_domain_filter: str = "*"


class PortalConfig(StrictModel):
    title: str = "Yoda"
    secret_key: str = Field("env://YODA_FLASK_SECRET_KEY", min_length=1)
    session_cookie_samesite: Literal["Strict", "Lax", "None"] = "Strict"
    permanent_session_lifetime: int = Field(1800, ge=60)
    session_file_dir: str = "/var/www/yoda/sessions"
    caching_enabled: bool = False
    log_api_call_duration: bool = False
    upload_part_files: bool = True
    monitor_output_dir: str = "/tmp"
    monitor_signal_file: str = "/var/www/yoda/show-tech.sig"
    text_file_extensions: list[str] = Field(
        default_factory=lambda: ["txt", "md", "csv", "json", "yaml", "xml"]
    )


class TokensModuleConfig(StrictModel):
    enabled: bool = True
    database_path: str = "/etc/irods/yoda-ruleset/accesstokens.db"
    database_password: str = "env://YODA_TOKEN_DB_PASSWORD"
    length: int = Field(32, ge=16, le=128)
    lifetime_hours: int = Field(72, ge=1)
    expiration_notification_hours: int = Field(24, ge=0)


class DataRequestModuleConfig(StrictModel):
    enabled: bool = False
    help_contact_name: str = ""
    help_contact_email: str = ""


class ArchiveModuleConfig(StrictModel):
    enabled: bool = False
    fqdn: str = ""
    minimum_bytes: int = Field(1073741824, ge=-1)
    maximum_bytes: int = Field(-1, ge=-1)
    resource: str = ""


class OpenSearchConfig(StrictModel):
    enabled: bool = False
    server: str = ""


class ModulesConfig(StrictModel):
    research_enabled: bool = True
    deposit_enabled: bool = False
    data_package_reference_enabled: bool = False
    datarequest: DataRequestModuleConfig = Field(default_factory=DataRequestModuleConfig)
    opensearch: OpenSearchConfig = Field(default_factory=OpenSearchConfig)
    tokens: TokensModuleConfig = Field(default_factory=TokensModuleConfig)
    archive: ArchiveModuleConfig = Field(default_factory=ArchiveModuleConfig)
    clienttools_enabled: bool = False
    report_enabled: bool = False
    async_checksum_enabled: bool = True
    async_checksum_delay_time: int = Field(0, ge=0)
    async_replication_delay_time: int = Field(0, ge=0)
    async_replication_max_rss: int = Field(1000000000, ge=0)
    async_revision_delay_time: int = Field(0, ge=0)
    async_revision_max_rss: int = Field(1000000000, ge=0)
    vault_copy_backoff_time: int = Field(300, ge=0)
    vault_copy_max_retries: int = Field(5, ge=1)
    vault_copy_multithread_enabled: bool = False
    deaccession_cooldown_days: int = Field(14, ge=0)
    inactivity_notification_enabled: bool = False
    inactivity_cutoff_months: int = Field(6, ge=1)
    measure_coverage: bool = False
    pregenerated_data_dir: str = "/var/lib/irods/yoda-pregenerated-data"
    temporary_files: list[str] = Field(default_factory=lambda: ["?_?*", "*.tmp"])

    @model_validator(mode="after")
    def validate_module_dependencies(self) -> ModulesConfig:
        if self.report_enabled and not self.clienttools_enabled:
            raise ValueError(
                "modules.report_enabled=true requires modules.clienttools_enabled=true."
            )
        if self.deposit_enabled and not self.data_package_reference_enabled:
            raise ValueError(
                "modules.deposit_enabled=true requires modules.data_package_reference_enabled=true."
            )
        if self.opensearch.enabled and not self.opensearch.server:
            raise ValueError(
                "modules.opensearch.server is required when modules.opensearch.enabled is true."
            )
        if self.archive.enabled and (not self.archive.fqdn or not self.archive.resource):
            raise ValueError(
                "modules.archive.fqdn and modules.archive.resource are required when archive is enabled."
            )
        return self


class SmtpConfig(StrictModel):
    notifications_enabled: bool = False
    sender_email: str = ""
    sender_name: str = "Yoda"
    reply_to: str = ""
    server: str = "smtp://localhost:25"
    username: str = ""
    password: str = ""
    auth: bool = True
    starttls: bool = True


class DataciteConfig(StrictModel):
    rest_api_url: str = "api.test.datacite.org"
    username: str = ""
    password: str = ""
    publisher: str = "Utrecht University"
    tls_verify: bool = True


class EpicConfig(StrictModel):
    enabled: bool = False
    url: str = ""
    handle_prefix: str = ""
    credentials_store: str = "/etc/irods/yoda-ruleset/credentials"


class EusIntegrationConfig(StrictModel):
    enabled: bool = True
    api_secret: str = "env://YODA_EUS_API_SECRET"
    db_password: str = "env://YODA_EUS_DB_PASSWORD"


class MatomoConfig(StrictModel):
    tracking_enabled: bool = False
    counter_enabled: bool = False
    server_fqdn: str = ""
    site_id: str = "1"

    @model_validator(mode="after")
    def validate_matomo(self) -> MatomoConfig:
        if (self.tracking_enabled or self.counter_enabled) and not self.server_fqdn:
            raise ValueError(
                "integrations.matomo.server_fqdn is required when tracking or counter is enabled."
            )
        return self


class AnubisConfig(StrictModel):
    enabled: bool = False
    redis_url: str = ""
    redis_password: str = ""
    ed25519_private_key: str = ""

    @model_validator(mode="after")
    def validate_anubis(self) -> AnubisConfig:
        if self.enabled and (not self.redis_password or not self.ed25519_private_key):
            raise ValueError(
                "integrations.anubis requires redis_password and ed25519_private_key when enabled."
            )
        return self


class IntegrationsConfig(StrictModel):
    smtp: SmtpConfig = Field(default_factory=SmtpConfig)
    datacite: DataciteConfig = Field(default_factory=DataciteConfig)
    epic: EpicConfig = Field(default_factory=EpicConfig)
    eus: EusIntegrationConfig = Field(default_factory=EusIntegrationConfig)
    matomo: MatomoConfig = Field(default_factory=MatomoConfig)
    anubis: AnubisConfig = Field(default_factory=AnubisConfig)


class CustomizationsConfig(StrictModel):
    """Formal extension points (replaces inline git-apply patching)."""

    default_schema: str = "default-3"
    schemas: list[str] = Field(default_factory=lambda: ["default-3"])
    theme: str = "uu"
    theme_path: str = "/var/www/yoda/themes"
    theme_mapping: dict[str, str] = Field(default_factory=dict)
    ruleset_extensions: list[str] = Field(default_factory=list)

    @field_validator("ruleset_extensions")
    @classmethod
    def validate_extension_names(cls, names: list[str]) -> list[str]:
        for name in names:
            if "/" in name or ".." in name:
                raise ValueError(f"Invalid ruleset extension name '{name}': path traversal not allowed.")
        return names


class YodaConfig(StrictModel):
    """Top-level Yoda declarative configuration (yoda.yaml)."""

    apiVersion: Literal["yoda.uu.nl/v1"] = "yoda.uu.nl/v1"
    kind: Literal["YodaInstanceConfig"] = "YodaInstanceConfig"
    instance: InstanceConfig
    endpoints: EndpointsConfig
    database: DatabaseContractConfig
    irods: IrodsConfig
    portal: PortalConfig = Field(default_factory=PortalConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    modules: ModulesConfig = Field(default_factory=ModulesConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    customizations: CustomizationsConfig = Field(default_factory=CustomizationsConfig)

    @model_validator(mode="after")
    def validate_cross_component_and_security(self) -> YodaConfig:
        # Cross-component consistency checks
        if self.auth.oidc.enabled and self.portal.session_cookie_samesite == "Strict":
            raise ValueError(
                "portal.session_cookie_samesite must be 'Lax' (not 'Strict') when auth.oidc.enabled is true."
            )

        if self.integrations.eus.enabled and not self.endpoints.eus_fqdn:
            raise ValueError(
                "endpoints.eus_fqdn is required when integrations.eus.enabled is true."
            )

        # Security invariants for non-development (or explicitly enabled) environments
        if self.instance.enforce_security_checks:
            self._check_security_invariants()

        return self

    def _check_security_invariants(self) -> None:
        errors: list[str] = []

        def check_secret(field_path: str, val: str) -> None:
            if not is_secret_reference(val) and val in INSECURE_DEFAULTS:
                errors.append(
                    f"Insecure default value for '{field_path}' in {self.instance.environment} environment."
                )

        check_secret("irods.admin_password", self.irods.admin_password)
        check_secret("database.password", self.database.password)
        check_secret("portal.secret_key", self.portal.secret_key)

        if self.modules.tokens.enabled:
            check_secret("modules.tokens.database_password", self.modules.tokens.database_password)

        if self.integrations.eus.enabled:
            check_secret("integrations.eus.api_secret", self.integrations.eus.api_secret)
            check_secret("integrations.eus.db_password", self.integrations.eus.db_password)

        if self.integrations.anubis.enabled:
            check_secret("integrations.anubis.redis_password", self.integrations.anubis.redis_password)
            check_secret(
                "integrations.anubis.ed25519_private_key",
                self.integrations.anubis.ed25519_private_key,
            )

        if self.irods.ssl_verify_server == "none":
            errors.append(
                "irods.ssl_verify_server cannot be 'none' in non-development environments (use 'cert' or 'hostname')."
            )

        if not self.integrations.datacite.tls_verify:
            errors.append("integrations.datacite.tls_verify must be true in non-development environments.")

        if self.integrations.eus.enabled and not self.endpoints.eus_api_tls_verify:
            errors.append("endpoints.eus_api_tls_verify must be true in non-development environments.")

        if self.auth.sram.enabled:
            if self.auth.sram.rest_api_url == "sram-mock.yoda.test":
                errors.append("auth.sram.rest_api_url cannot point to sram-mock.yoda.test in production.")
            if not self.auth.sram.tls_verify:
                errors.append("auth.sram.tls_verify must be true in non-development environments.")
            for uri_field in ("auth_base_uri", "token_uri", "userinfo_uri", "jwks_uri", "jwt_issuer"):
                uri_val = getattr(self.auth.sram.oidc, uri_field, "")
                if "https://oauth.wiremockapi.cloud" in uri_val:
                    errors.append(f"auth.sram.oidc.{uri_field} cannot point to WireMock in production.")

        if self.modules.measure_coverage:
            errors.append("modules.measure_coverage must be false in non-development environments.")

        if errors:
            raise ValueError("Security validation failed:\n  - " + "\n  - ".join(errors))


class ImageSpec(StrictModel):
    image: str
    tag: str
    digest: str | None = None
    maturity: str | None = None

    @property
    def full_ref(self) -> str:
        if self.digest:
            return f"{self.image}:{self.tag}@{self.digest}"
        return f"{self.image}:{self.tag}"


class ReleaseBOM(BaseModel):
    """Machine-readable Release Bill of Materials."""

    model_config = ConfigDict(extra="allow")

    apiVersion: Literal["yoda.uu.nl/v1"] = "yoda.uu.nl/v1"
    kind: Literal["ReleaseBOM"] = "ReleaseBOM"
    release: dict[str, Any]
    external_contracts: dict[str, Any]
    web_tier_images: dict[str, ImageSpec]
    data_tier_packages: dict[str, Any]
    data_tier_images: dict[str, ImageSpec] = Field(default_factory=dict)


def load_config(path: str | Path) -> YodaConfig:
    """Load and strictly validate a yoda.yaml configuration file."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}, got {type(data).__name__}.")
    return YodaConfig.model_validate(data)


def load_bom(path: str | Path) -> ReleaseBOM:
    """Load and validate a release-bom.yaml file."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}, got {type(data).__name__}.")
    return ReleaseBOM.model_validate(data)
