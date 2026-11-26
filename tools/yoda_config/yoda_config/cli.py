"""CLI entry point for yoda-config (validate, diff, render, import-ansible, preflight, schema)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from .importer import import_ansible_environment
from .preflight import run_preflight
from .renderer import diff_rendered, render_all, write_rendered
from .schema import YodaConfig, load_bom, load_config


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    print(
        f"VALID: {cfg.instance.name} ({cfg.instance.environment}, yoda {cfg.instance.yoda_version})"
    )

    if args.bom:
        try:
            bom = load_bom(args.bom)
            print(f"BOM OK: release {bom.release.get('yoda_version', 'unknown')}")
        except (ValidationError, ValueError, FileNotFoundError) as exc:
            print(f"INVALID BOM: {exc}", file=sys.stderr)
            return 1

    if args.preflight:
        results = run_preflight(cfg)
        failed = False
        for res in results:
            status = "OK" if res.ok else "FAIL"
            print(f"  [{status}] {res.name}: {res.detail}")
            if not res.ok:
                failed = True
        if failed:
            return 2

    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    bom = load_bom(args.bom) if args.bom else None
    artifacts = render_all(cfg, bom=bom, with_reference_edge=args.with_reference_edge)
    written = write_rendered(artifacts, args.out)
    for path in written:
        print(f"Wrote {path}")
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    bom = load_bom(args.bom) if args.bom else None
    artifacts = render_all(cfg, bom=bom, with_reference_edge=args.with_reference_edge)
    diff_text = diff_rendered(artifacts, args.target)
    if diff_text:
        print(diff_text)
        return 0
    print("No changes.")
    return 0


def _cmd_import_ansible(args: argparse.Namespace) -> int:
    result = import_ansible_environment(args.env_dir)
    rendered_yaml = yaml.safe_dump(
        result.config.model_dump(mode="json", exclude_none=True),
        sort_keys=False,
    )
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered_yaml, encoding="utf-8")
        print(f"Imported {args.env_dir} -> {out_path}")
    else:
        print(rendered_yaml)

    if result.warnings:
        print("\nMigration warnings:", file=sys.stderr)
        for warning in result.warnings:
            print(f"  - {warning}", file=sys.stderr)
    return 0


def _cmd_preflight(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    results = run_preflight(cfg, timeout=args.timeout)
    failed = False
    for res in results:
        status = "OK" if res.ok else "FAIL"
        print(f"[{status}] {res.name}: {res.detail}")
        if not res.ok:
            failed = True
    return 2 if failed else 0


def _cmd_schema(args: argparse.Namespace) -> int:
    schema_json = json.dumps(YodaConfig.model_json_schema(), indent=2)
    if args.out:
        Path(args.out).write_text(schema_json + "\n", encoding="utf-8")
        print(f"Wrote JSON Schema to {args.out}")
    else:
        print(schema_json)
    return 0


def _cmd_bump_bom(args: argparse.Namespace) -> int:
    bom_path = Path(args.bom)
    bom = load_bom(bom_path)
    if args.component not in bom.web_tier_images:
        print(
            f"ERROR: Unknown web_tier_images component '{args.component}'. "
            f"Valid: {', '.join(sorted(bom.web_tier_images.keys()))}",
            file=sys.stderr,
        )
        return 1

    raw = yaml.safe_load(bom_path.read_text(encoding="utf-8"))
    target = raw["web_tier_images"][args.component]
    if args.tag:
        target["tag"] = args.tag
    if args.digest:
        if not args.digest.startswith("sha256:"):
            print("ERROR: --digest must start with 'sha256:'", file=sys.stderr)
            return 1
        target["digest"] = args.digest

    # Re-validate updated BOM before writing
    bom_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    load_bom(bom_path)
    print(f"Updated {args.component} in {bom_path} (tag={target['tag']}, digest={target['digest']})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yoda-config",
        description="Typed configuration validator, Ansible importer, and renderer for Yoda.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", help="Validate yoda.yaml (and optional BOM / preflight).")
    p_val.add_argument("config", help="Path to yoda.yaml")
    p_val.add_argument("--bom", help="Optional path to release-bom.yaml")
    p_val.add_argument("--preflight", action="store_true", help="Run live external contract checks")
    p_val.set_defaults(func=_cmd_validate)

    p_ren = sub.add_parser("render", help="Render component configs (flask.cfg, rules_uu.cfg, compose).")
    p_ren.add_argument("config", help="Path to yoda.yaml")
    p_ren.add_argument("--bom", help="Optional path to release-bom.yaml for web-tier Compose")
    p_ren.add_argument(
        "--with-reference-edge",
        action="store_true",
        help="Also render reference edge reverse-proxy (Caddy) + Anubis/Redis stack",
    )
    p_ren.add_argument("-o", "--out", required=True, help="Output directory")
    p_ren.set_defaults(func=_cmd_render)

    p_diff = sub.add_parser("diff", help="Diff desired rendered configs against a target directory.")
    p_diff.add_argument("config", help="Path to yoda.yaml")
    p_diff.add_argument("--bom", help="Optional path to release-bom.yaml")
    p_diff.add_argument(
        "--with-reference-edge",
        action="store_true",
        help="Also diff reference edge reverse-proxy + Anubis/Redis stack",
    )
    p_diff.add_argument("-t", "--target", required=True, help="Directory with currently deployed configs")
    p_diff.set_defaults(func=_cmd_diff)

    p_imp = sub.add_parser("import-ansible", help="Convert an Ansible environment to yoda.yaml.")
    p_imp.add_argument("env_dir", help="Path to Ansible environment dir (containing group_vars/)")
    p_imp.add_argument("-o", "--out", help="Output path for yoda.yaml (defaults to stdout)")
    p_imp.set_defaults(func=_cmd_import_ansible)

    p_pre = sub.add_parser("preflight", help="Run non-destructive live contract checks.")
    p_pre.add_argument("config", help="Path to yoda.yaml")
    p_pre.add_argument("--timeout", type=float, default=3.0, help="Connection timeout in seconds")
    p_pre.set_defaults(func=_cmd_preflight)

    p_sch = sub.add_parser("schema", help="Export JSON Schema for yoda.yaml.")
    p_sch.add_argument("-o", "--out", help="Output file path (defaults to stdout)")
    p_sch.set_defaults(func=_cmd_schema)

    p_bump = sub.add_parser("bump-bom", help="Pin or update a web-tier image tag/digest in release-bom.yaml.")
    p_bump.add_argument("--bom", required=True, help="Path to release-bom.yaml")
    p_bump.add_argument("--component", required=True, help="Component key (portal, davrods, public, eus)")
    p_bump.add_argument("--tag", help="New image tag")
    p_bump.add_argument("--digest", help="New immutable image digest (sha256:...)")
    p_bump.set_defaults(func=_cmd_bump_bom)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
