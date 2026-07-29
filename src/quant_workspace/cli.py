from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from quant_workspace.loader import load_workspace


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
                **{k: str(getattr(p, k)) for k in ("outputs", "state", "data", "notes", "reports") if getattr(p, k)},
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
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
