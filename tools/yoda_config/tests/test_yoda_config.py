"""Tests for yoda-config schema, security invariants, Ansible importer, renderer, and CLI."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from yoda_config.cli import main as cli_main
from yoda_config.importer import import_ansible_environment
from yoda_config.renderer import diff_rendered, render_all, write_rendered
from yoda_config.schema import YodaConfig, load_bom, load_config

REPO_ROOT = Path(__file__).resolve().parents[3]
BOM_PATH = REPO_ROOT / "distribution" / "release-bom.yaml"


def make_prod_config_dict() -> dict:
    return {
        "apiVersion": "yoda.uu.nl/v1",
        "kind": "YodaInstanceConfig",
        "instance": {
            "name": "uu-prod",
            "environment": "production",
            "yoda_version": "2.2.0",
            "yoda_prefix": "UU01",
        },
        "endpoints": {
            "portal_fqdn": "portal.yoda.uu.nl",
            "davrods_fqdn": "data.yoda.uu.nl",
            "public_fqdn": "public.yoda.uu.nl",
            "eus_fqdn": "eus.yoda.uu.nl",
            "eus_api_fqdn": "eus.yoda.uu.nl",
            "eus_api_port": 8443,
            "eus_api_tls_verify": True,
        },
        "database": {
            "host": "pg.internal.uu.nl",
            "port": 5432,
            "name": "ICAT",
            "user": "irods",
            "password": "env://YODA_DB_PASSWORD",
            "ssl_mode": "require",
        },
        "irods": {
            "zone": "uuProdZone",
            "icat_fqdn": "icat.internal.uu.nl",
            "admin_password": "env://YODA_IRODS_ADMIN_PASSWORD",
            "ssl_verify_server": "cert",
            "default_resource": "irodsResc",
        },
        "portal": {
            "title": "Yoda Production",
            "secret_key": "env://YODA_FLASK_SECRET_KEY",
            "session_cookie_samesite": "Strict",
        },
        "integrations": {
            "datacite": {
                "rest_api_url": "api.datacite.org",
                "tls_verify": True,
            },
            "eus": {
                "enabled": True,
                "api_secret": "env://YODA_EUS_API_SECRET",
                "db_password": "env://YODA_EUS_DB_PASSWORD",
            },
        },
    }


def test_valid_production_config_and_bom() -> None:
    cfg = YodaConfig.model_validate(make_prod_config_dict())
    assert cfg.instance.name == "uu-prod"
    assert cfg.instance.enforce_security_checks is True

    bom = load_bom(BOM_PATH)
    assert "portal" in bom.web_tier_images
    assert "davrods" in bom.web_tier_images
    assert bom.data_tier_packages["irods"]["server_version"] == "5.0.2"


def test_unknown_key_rejected_early() -> None:
    data = make_prod_config_dict()
    data["portal"]["typo_key_should_fail"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        YodaConfig.model_validate(data)


def test_production_blocks_insecure_defaults() -> None:
    data = make_prod_config_dict()
    data["irods"]["admin_password"] = "rods"
    data["database"]["password"] = "irodsdev"
    data["irods"]["ssl_verify_server"] = "none"
    with pytest.raises(ValidationError, match="Security validation failed"):
        YodaConfig.model_validate(data)


def test_oidc_requires_lax_samesite() -> None:
    data = make_prod_config_dict()
    data["auth"] = {
        "oidc": {
            "enabled": True,
            "client_id": "yoda-client",
            "client_secret": "env://OIDC_SECRET",
            "auth_base_uri": "https://idp.example.org/auth",
            "token_uri": "https://idp.example.org/token",
            "jwks_uri": "https://idp.example.org/jwks",
            "jwt_issuer": "https://idp.example.org",
        }
    }
    with pytest.raises(ValidationError, match="session_cookie_samesite must be 'Lax'"):
        YodaConfig.model_validate(data)

    data["portal"]["session_cookie_samesite"] = "Lax"
    cfg = YodaConfig.model_validate(data)
    assert cfg.auth.oidc.enabled is True


def test_module_dependency_invariants() -> None:
    data = make_prod_config_dict()
    data["modules"] = {"report_enabled": True, "clienttools_enabled": False}
    with pytest.raises(ValidationError, match="requires modules.clienttools_enabled=true"):
        YodaConfig.model_validate(data)

    data2 = make_prod_config_dict()
    data2["modules"] = {"deposit_enabled": True, "data_package_reference_enabled": False}
    with pytest.raises(ValidationError, match="requires modules.data_package_reference_enabled=true"):
        YodaConfig.model_validate(data2)


def test_extension_path_traversal_rejected() -> None:
    data = make_prod_config_dict()
    data["customizations"] = {"ruleset_extensions": ["../evil-ruleset"]}
    with pytest.raises(ValidationError, match="path traversal not allowed"):
        YodaConfig.model_validate(data)


@pytest.mark.parametrize("env_name", ["full", "allinone"])
def test_import_real_ansible_environments(env_name: str, tmp_path: Path) -> None:
    env_dir = REPO_ROOT / "environments" / "development" / env_name
    result = import_ansible_environment(env_dir)
    assert result.config.instance.environment == "development"
    assert result.config.endpoints.portal_fqdn

    # Round-trip write & reload through load_config
    out_yaml = tmp_path / f"{env_name}.yoda.yaml"
    out_yaml.write_text(
        yaml.safe_dump(result.config.model_dump(mode="json", exclude_none=True), sort_keys=False),
        encoding="utf-8",
    )
    reloaded = load_config(out_yaml)
    assert reloaded.instance.name == result.config.instance.name


def test_render_and_diff_determinism(tmp_path: Path) -> None:
    cfg = YodaConfig.model_validate(make_prod_config_dict())
    bom = load_bom(BOM_PATH)
    artifacts = render_all(cfg, bom=bom)

    assert "portal/flask.cfg" in artifacts
    assert "icat/rules_uu.cfg" in artifacts
    assert "web-tier/docker-compose.yml" in artifacts
    assert "IRODS_ICAT_HOSTNAME = 'icat.internal.uu.nl'" in artifacts["portal/flask.cfg"]
    assert "environment                    = 'production'" in artifacts["icat/rules_uu.cfg"]
    assert "ghcr.io/utrechtuniversity/yoda-portal:development" in artifacts["web-tier/docker-compose.yml"]

    write_rendered(artifacts, tmp_path)
    assert diff_rendered(artifacts, tmp_path) == ""

    # Mutate config and verify diff detects exact change
    data2 = copy.deepcopy(make_prod_config_dict())
    data2["portal"]["title"] = "Yoda Production v2"
    cfg2 = YodaConfig.model_validate(data2)
    diff_text = diff_rendered(render_all(cfg2, bom=bom), tmp_path)
    assert "-PORTAL_TITLE_TEXT   = 'Yoda Production'" in diff_text
    assert "+PORTAL_TITLE_TEXT   = 'Yoda Production v2'" in diff_text


def test_cli_end_to_end(tmp_path: Path) -> None:
    yoda_yaml = tmp_path / "full.yoda.yaml"
    rc = cli_main(
        [
            "import-ansible",
            str(REPO_ROOT / "environments" / "development" / "full"),
            "-o",
            str(yoda_yaml),
        ]
    )
    assert rc == 0
    assert yoda_yaml.is_file()

    rc = cli_main(["validate", str(yoda_yaml), "--bom", str(BOM_PATH)])
    assert rc == 0

    rendered_dir = tmp_path / "rendered"
    rc = cli_main(["render", str(yoda_yaml), "--bom", str(BOM_PATH), "-o", str(rendered_dir)])
    assert rc == 0
    assert (rendered_dir / "portal" / "flask.cfg").is_file()
    assert (rendered_dir / "icat" / "rules_uu.cfg").is_file()
    assert (rendered_dir / "web-tier" / "docker-compose.yml").is_file()

    rc = cli_main(["diff", str(yoda_yaml), "--bom", str(BOM_PATH), "-t", str(rendered_dir)])
    assert rc == 0

    schema_file = tmp_path / "schema.json"
    rc = cli_main(["schema", "-o", str(schema_file)])
    assert rc == 0
    assert schema_file.is_file()


def test_reference_edge_with_and_without_anubis(tmp_path: Path) -> None:
    # 1. Without Anubis: edge-proxy routes directly to public:8080
    base_data = make_prod_config_dict()
    cfg_no_anubis = YodaConfig.model_validate(base_data)
    artifacts = render_all(cfg_no_anubis, bom=load_bom(BOM_PATH), with_reference_edge=True)

    assert "edge/Caddyfile" in artifacts
    assert "edge/docker-compose.edge.yml" in artifacts
    assert "reverse_proxy public:8080" in artifacts["edge/Caddyfile"]
    compose_no_anubis = yaml.safe_load(artifacts["edge/docker-compose.edge.yml"])
    assert set(compose_no_anubis["services"].keys()) == {"edge-proxy"}

    # 2. With Anubis + anonymous DavRODS: routes public & anon davrods through Anubis + Redis
    anubis_data = copy.deepcopy(base_data)
    anubis_data["endpoints"]["davrods_anonymous_enabled"] = True
    anubis_data["endpoints"]["davrods_anonymous_fqdn"] = "public.data.yoda.uu.nl"
    anubis_data["integrations"]["anubis"] = {
        "enabled": True,
        "redis_password": "env://ANUBIS_REDIS_PASSWORD",
        "ed25519_private_key": "env://ANUBIS_ED25519_PRIVATE_KEY",
    }
    anubis_yaml = tmp_path / "anubis.yoda.yaml"
    anubis_yaml.write_text(yaml.safe_dump(anubis_data, sort_keys=False), encoding="utf-8")

    out_dir = tmp_path / "rendered_edge"
    rc = cli_main(
        [
            "render",
            str(anubis_yaml),
            "--bom",
            str(BOM_PATH),
            "--with-reference-edge",
            "-o",
            str(out_dir),
        ]
    )
    assert rc == 0
    caddy = (out_dir / "edge" / "Caddyfile").read_text(encoding="utf-8")
    assert "reverse_proxy anubis-public:8923" in caddy
    assert "reverse_proxy anubis-davrods:8924" in caddy

    compose_anubis = yaml.safe_load((out_dir / "edge" / "docker-compose.edge.yml").read_text(encoding="utf-8"))
    assert set(compose_anubis["services"].keys()) == {
        "edge-proxy",
        "anubis-redis",
        "anubis-public",
        "anubis-davrods",
    }

    # Verify shared yoda-web network matches between web-tier and edge Compose files
    web_compose = yaml.safe_load((out_dir / "web-tier" / "docker-compose.yml").read_text(encoding="utf-8"))
    assert web_compose["networks"]["yoda-web"]["name"] == compose_anubis["networks"]["yoda-web"]["name"]
    assert compose_anubis["networks"]["yoda-web"]["external"] is True

    # Verify remaining web-tier configs are rendered
    assert (out_dir / "eus" / "eus.cfg").is_file()
    assert (out_dir / "public" / "moai-settings.ini").is_file()
    assert (out_dir / "davrods" / "davrods-vhost.conf").is_file()
    davrods_conf = (out_dir / "davrods" / "davrods-vhost.conf").read_text(encoding="utf-8")
    assert "DavRodsAnonymousMode On" in davrods_conf
    assert "ServerName public.data.yoda.uu.nl" in davrods_conf


def test_bump_bom_updates_digest_and_validates(tmp_path: Path) -> None:
    bom_copy = tmp_path / "release-bom.yaml"
    bom_copy.write_text(BOM_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    digest = "sha256:" + ("a" * 64)
    rc = cli_main(
        [
            "bump-bom",
            "--bom",
            str(bom_copy),
            "--component",
            "portal",
            "--tag",
            "2.2.1-sec.1",
            "--digest",
            digest,
        ]
    )
    assert rc == 0

    updated = load_bom(bom_copy)
    assert updated.web_tier_images["portal"].tag == "2.2.1-sec.1"
    assert updated.web_tier_images["portal"].digest == digest
    assert updated.web_tier_images["portal"].full_ref == f"ghcr.io/utrechtuniversity/yoda-portal:2.2.1-sec.1@{digest}"


