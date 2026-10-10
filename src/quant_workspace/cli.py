from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

from quant_workspace.capabilities import load_capabilities, source_inventory
from quant_workspace.delivery import (
    activate_candidate,
    create_candidate,
    prepare_candidate,
    run_acceptance,
)
from quant_workspace.loader import load_workspace
from quant_workspace.m7_certification import load_m7_certification, validate_m7_certification
from quant_workspace.runtime_readiness import (
    bootstrap,
    check_runtime,
    create_profile,
    write_profile,
)
from quant_workspace.stack_manifest import (
    StackManifestReleaseError,
    discover_stack,
    load_stack_manifest,
    validate_stack_manifest,
    write_stack_manifest,
)
from quant_workspace.transfer import (
    create_transfer_package,
    restore_transfer_package,
    verify_transfer_package,
)


def _default_config() -> Path:
    return Path("configs/default.workspace.yaml")


def cmd_show(args: argparse.Namespace) -> int:
    ws = load_workspace(Path(args.config), root_override=args.root or None)
    payload = {
        "root": str(ws.root),
        "config": str(ws.config_path),
        "projects": {
            name: {
                "repo": str(p.repo),
                **{
                    k: str(getattr(p, k))
                    for k in ("outputs", "state", "data", "notes", "reports")
                    if getattr(p, k)
                },
                **{k: str(v) for k, v in p.extra.items()},
            }
            for name, p in ws.projects.items()
        },
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def cmd_capabilities(args: argparse.Namespace) -> int:
    catalog = load_capabilities()
    if args.inventory:
        ws = load_workspace(Path(args.config), root_override=args.root or None)
        inventory = source_inventory(catalog, ws.root)
        print(json.dumps(inventory, indent=2, ensure_ascii=False))
        return 0 if inventory["source_inventory_complete"] else 2
    print(json.dumps(catalog, indent=2, ensure_ascii=False))
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    ws = load_workspace(Path(args.config), root_override=args.root or None)
    print(ws.path(args.project, args.key))
    return 0


def cmd_runtime(args: argparse.Namespace) -> int:
    try:
        workspace = load_workspace(Path(args.config), root_override=args.root or None)
        if args.command == "runtime-profile":
            profile = create_profile(
                workspace, args.projects, python=args.python_spec, environment=args.environment
            )
            write_profile(Path(args.out), profile)
            payload = {"path": args.out, "profile": profile}
        elif args.command == "doctor":
            payload = check_runtime(Path(args.profile), workspace.root)
        else:
            payload = bootstrap(
                Path(args.profile), workspace.root, args.project, execute=args.execute
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2 if payload.get("status") == "blocked" else 0
    except (KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2


def cmd_delivery(args: argparse.Namespace) -> int:
    try:
        if args.command == "release-candidate":
            payload = create_candidate(
                Path(args.profile),
                Path(args.source_root),
                Path(args.acceptance_suite),
                Path(args.state_contract),
                Path(args.out),
                state_dir=Path(args.state_dir) if args.state_dir else None,
                created_at=args.created_at or None,
            )
        elif args.command == "prepare-release":
            payload = prepare_candidate(
                Path(args.candidate),
                Path(args.source_root),
                Path(args.destination),
                execute=args.execute,
                build_environments=args.build_environments,
            )
        elif args.command == "accept-release":
            payload = run_acceptance(
                Path(args.candidate), Path(args.prepared_root), Path(args.out), timeout=args.timeout
            )
        else:
            expected = None if args.expected_current == "none" else args.expected_current
            payload = activate_candidate(
                Path(args.candidate),
                Path(args.evidence),
                Path(args.prepared_root),
                Path(args.state_dir),
                expected_current=expected,
                action="rollback" if args.command == "rollback-release" else "activate",
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2 if payload.get("status") == "blocked" else 0
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2


def cmd_transfer(args: argparse.Namespace) -> int:
    try:
        if args.command == "transfer-package":
            payload = create_transfer_package(
                Path(args.spec), Path(args.source_root), Path(args.out)
            )
        elif args.command == "verify-transfer":
            payload = verify_transfer_package(Path(args.archive))
        else:
            payload = restore_transfer_package(Path(args.archive), Path(args.destination))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2


def cmd_lab_config(args: argparse.Namespace) -> int:
    ws = load_workspace(Path(args.config), root_override=args.root or None)
    out = ws.lab_workspace_yaml()
    text = yaml.safe_dump(out, allow_unicode=True, sort_keys=False)
    out_path = Path(args.out) if args.out else None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"wrote {out_path}")
    else:
        print(text, end="")
    return 0


def cmd_stack_manifest(args: argparse.Namespace) -> int:
    ws = load_workspace(Path(args.config), root_override=args.root or None)
    try:
        manifest = discover_stack(ws, mode=args.mode, created_at=args.created_at or None)
    except StackManifestReleaseError as exc:
        print(json.dumps(exc.result.to_dict(), indent=2, ensure_ascii=False), file=sys.stderr)
        return 2
    try:
        write_stack_manifest(Path(args.out), manifest)
    except (OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    result = validate_stack_manifest(manifest)
    print(
        json.dumps(
            {
                "path": str(Path(args.out)),
                "manifest_hash": manifest.manifest_hash,
                **result.to_dict(),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def cmd_verify_stack(args: argparse.Namespace) -> int:
    try:
        manifest = load_stack_manifest(Path(args.path))
    except (TypeError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    result = validate_stack_manifest(manifest)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0 if result.valid else 2


def cmd_verify_m7_certification(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        certification = load_m7_certification(path)
    except (TypeError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    result = validate_m7_certification(certification, evidence_root=path.parent)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0 if result.valid else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quant-workspace", description="Resolve quant stack paths")
    p.add_argument("--config", default=str(_default_config()), dest="config")
    p.add_argument("--root", default="", help="Override workspace root", dest="root")
    sub = p.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="Print resolved workspace JSON")
    show.set_defaults(func=cmd_show)

    capabilities = sub.add_parser("capabilities", help="Show declared capabilities and boundaries")
    capabilities.add_argument(
        "--inventory",
        action="store_true",
        help="Check local source presence, Git and evidence only",
    )
    capabilities.set_defaults(func=cmd_capabilities)

    runtime = sub.add_parser("runtime-profile", help="Pin selected clean checkouts and lock bytes")
    runtime.add_argument("--projects", nargs="+", required=True)
    runtime.add_argument("--python-spec", default=">=3.12,<3.13")
    runtime.add_argument("--environment", default=".venv")
    runtime.add_argument("--out", required=True)
    runtime.set_defaults(func=cmd_runtime)
    doctor = sub.add_parser(
        "doctor", help="Read-only source, interpreter and installed-lock checks"
    )
    doctor.add_argument("--profile", required=True)
    doctor.set_defaults(func=cmd_runtime)
    install = sub.add_parser("bootstrap-env", help="Preview or create a new pinned environment")
    install.add_argument("--profile", required=True)
    install.add_argument("--project", required=True)
    install.add_argument(
        "--execute", action="store_true", help="Explicitly create and install a new environment"
    )
    install.set_defaults(func=cmd_runtime)

    candidate = sub.add_parser(
        "release-candidate", help="Pin a clean ready stack, state contract, and acceptance suite"
    )
    candidate.add_argument("--profile", required=True)
    candidate.add_argument("--source-root", required=True)
    candidate.add_argument("--acceptance-suite", required=True)
    candidate.add_argument("--state-contract", required=True)
    candidate.add_argument("--state-dir", default="")
    candidate.add_argument("--created-at", default="")
    candidate.add_argument("--out", required=True)
    candidate.set_defaults(func=cmd_delivery)

    prepare = sub.add_parser(
        "prepare-release", help="Plan or locally clone a candidate into a fresh directory"
    )
    prepare.add_argument("--candidate", required=True)
    prepare.add_argument("--source-root", required=True)
    prepare.add_argument("--destination", required=True)
    prepare.add_argument("--execute", action="store_true")
    prepare.add_argument("--build-environments", action="store_true")
    prepare.set_defaults(func=cmd_delivery)

    accept = sub.add_parser(
        "accept-release", help="Run candidate-bound compatibility and research acceptance"
    )
    accept.add_argument("--candidate", required=True)
    accept.add_argument("--prepared-root", required=True)
    accept.add_argument("--out", required=True)
    accept.add_argument("--timeout", type=int, default=900)
    accept.set_defaults(func=cmd_delivery)

    for name, help_text in (
        ("activate-release", "CAS-switch the current release pointer after acceptance"),
        ("rollback-release", "CAS-switch to an accepted older release without changing data"),
    ):
        switch = sub.add_parser(name, help=help_text)
        switch.add_argument("--candidate", required=True)
        switch.add_argument("--evidence", required=True)
        switch.add_argument("--prepared-root", required=True)
        switch.add_argument("--state-dir", required=True)
        switch.add_argument(
            "--expected-current", required=True, help="Expected candidate SHA-256, or 'none'"
        )
        switch.set_defaults(func=cmd_delivery)

    package = sub.add_parser(
        "transfer-package", help="Create a hashed migration archive from an explicit allowlist"
    )
    package.add_argument("--spec", required=True)
    package.add_argument("--source-root", required=True)
    package.add_argument("--out", required=True)
    package.set_defaults(func=cmd_transfer)

    verify_transfer = sub.add_parser(
        "verify-transfer", help="Verify migration manifest, entry set, hashes, and secret exclusions"
    )
    verify_transfer.add_argument("--archive", required=True)
    verify_transfer.set_defaults(func=cmd_transfer)

    restore_transfer = sub.add_parser(
        "restore-transfer", help="Restore a verified migration archive into a new directory"
    )
    restore_transfer.add_argument("--archive", required=True)
    restore_transfer.add_argument("--destination", required=True)
    restore_transfer.set_defaults(func=cmd_transfer)

    path = sub.add_parser("path", help="Print one resolved path")
    path.add_argument("project")
    path.add_argument("key", nargs="?", default="repo")
    path.set_defaults(func=cmd_path)

    lab = sub.add_parser("lab-config", help="Emit quant-lab workspace YAML")
    lab.add_argument("--out", default="", help="Write YAML to file")
    lab.set_defaults(func=cmd_lab_config)

    stack = sub.add_parser(
        "stack-manifest", help="Discover and atomically write StackManifest 1.0.0"
    )
    stack.add_argument("--mode", choices=("audit", "release"), required=True)
    stack.add_argument(
        "--out", required=True, help="New manifest path; existing files are never replaced"
    )
    stack.add_argument("--created-at", default="", help="Timezone-aware ISO-8601 timestamp")
    stack.set_defaults(func=cmd_stack_manifest)

    verify = sub.add_parser(
        "verify-stack", help="Verify a StackManifest file and its canonical hash"
    )
    verify.add_argument("path")
    verify.set_defaults(func=cmd_verify_stack)

    m7 = sub.add_parser(
        "verify-m7-certification",
        help="Verify canonical M7 performance, CI, and market-data evidence",
    )
    m7.add_argument("path")
    m7.set_defaults(func=cmd_verify_m7_certification)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
