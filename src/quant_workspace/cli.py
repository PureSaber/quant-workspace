from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from quant_workspace.loader import load_workspace
from quant_workspace.stack_manifest import (
    StackManifestReleaseError,
    discover_stack,
    load_stack_manifest,
    validate_stack_manifest,
    write_stack_manifest,
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


def cmd_path(args: argparse.Namespace) -> int:
    ws = load_workspace(Path(args.config), root_override=args.root or None)
    print(ws.path(args.project, args.key))
    return 0


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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quant-workspace", description="Resolve quant stack paths")
    p.add_argument("--config", default=str(_default_config()), dest="config")
    p.add_argument("--root", default="", help="Override workspace root", dest="root")
    sub = p.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="Print resolved workspace JSON")
    show.set_defaults(func=cmd_show)

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
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
