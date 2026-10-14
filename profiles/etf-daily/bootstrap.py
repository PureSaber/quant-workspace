"""Install the complete ETF stack, verifying actual installed VCS/import identities."""

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROFILE = Path(__file__).resolve().parent
MODULES = {
    name: name.replace("-", "_")
    for name in (
        "quant-data-kit",
        "quant-factors",
        "quant-lab",
        "quant-execution",
        "quant-portfolio",
        "quant-risk-monitor",
        "a-share-multifactor",
        "quant-report-hub",
        "quant-pipeline",
        "quant-workspace",
        "quant-studio",
    )
}


def call(*args):
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    subprocess.run([str(a) for a in args], check=True, env=env)


def installed_identity(stack):
    result = {}
    for name, module in MODULES.items():
        entry = Path(importlib.import_module(module).__file__).resolve()
        distribution = importlib.metadata.distribution(name)
        source = json.loads(distribution.read_text("direct_url.json") or "{}")
        if not entry.is_relative_to(Path(sys.prefix).resolve()):
            raise ValueError(f"{name}: import is outside the selected environment")
        if (
            source.get("dir_info", {}).get("editable")
            or source.get("vcs_info", {}).get("commit_id") != stack["repositories"][name]
        ):
            raise ValueError(f"{name}: installed VCS commit differs from pinned stack")
        tree = hashlib.sha256()
        for path in sorted(entry.parent.rglob("*")):
            if path.is_file() and path.suffix in {
                ".py",
                ".json",
                ".yaml",
                ".yml",
                ".html",
                ".css",
                ".js",
            }:
                tree.update(path.relative_to(entry.parent).as_posix().encode())
                tree.update(hashlib.sha256(path.read_bytes()).digest())
        result[name] = {
            "commit": stack["repositories"][name],
            "import": str(entry),
            "tree_sha256": tree.hexdigest(),
        }
    return {
        "schema": "quant.etf-installed-stack/v1",
        "python": sys.executable,
        "repositories": result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--verify-installed", action="store_true")
    args = parser.parse_args()
    stack = json.loads((PROFILE / "stack.json").read_text(encoding="utf-8"))
    lock = PROFILE / "requirements.lock"
    if stack["schema"] != "quant.etf-stack/v1" or set(stack["repositories"]) != set(MODULES):
        raise ValueError("Incomplete ETF source profile")
    if any(not re.fullmatch("[a-f0-9]{40}", v) for v in stack["repositories"].values()):
        raise ValueError("All source references must be full commits")
    if hashlib.sha256(lock.read_bytes()).hexdigest() != stack["requirements_sha256"]:
        raise ValueError("Dependency lock changed")
    if args.verify_installed:
        actual = installed_identity(stack)
        receipt = args.root / "installed-stack.json"
        if receipt.exists() and json.loads(receipt.read_text(encoding="utf-8")) != actual:
            raise ValueError("Installed package tree differs from installation receipt")
        print(json.dumps(actual, ensure_ascii=False, indent=2))
        return
    root = args.root.resolve()
    if root.exists():
        raise FileExistsError("Use a new runtime directory; preserve prior installations")
    if sys.version_info[:2] != (3, 12):
        raise ValueError("ETF runtime requires Python 3.12")
    root.mkdir(parents=True)
    call(sys.executable, "-m", "venv", root / "env")
    python = root / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    call(python, "-m", "pip", "install", "--disable-pip-version-check", "-r", lock)
    for name, commit in stack["repositories"].items():
        if args.source_root:
            source = (args.source_root / name).resolve()
            actual = subprocess.check_output(
                ["git", "-C", str(source), "rev-parse", commit + "^{commit}"], text=True
            ).strip()
            if actual != commit:
                raise ValueError(f"{name}: checkout differs from stack")
            url = source.as_uri()
        else:
            url = f"https://github.com/PureSaber/{name}.git"
        call(
            python,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            f"{name} @ git+{url}@{commit}",
        )
    call(python, "-m", "pip", "check")
    receipt = subprocess.check_output(
        [
            str(python),
            "-I",
            str(Path(__file__).resolve()),
            "--root",
            str(root),
            "--verify-installed",
        ],
        text=True,
        encoding="utf-8",
    )
    (root / "installed-stack.json").write_text(receipt, encoding="utf-8")
    shutil.copytree(PROFILE, root / "profile")


if __name__ == "__main__":
    main()
