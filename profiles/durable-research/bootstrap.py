"""Install the pinned durable research stack into a new permanent directory."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROFILE = Path(__file__).resolve().parent
MODULES = {
    "quant-data-kit": "quant_data_kit.artifacts",
    "quant-factors": "quant_factors.cache",
    "quant-lab": "quant_lab.rolling_research",
    "quant-pipeline": "quant_pipeline.durable_worker",
    "quant-workspace": "quant_workspace",
    "quant-studio": "quant_studio",
}


def clean_environment() -> dict[str, str]:
    return {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}
    }


def call(*args: object, cwd: Path | None = None) -> None:
    subprocess.run([str(arg) for arg in args], cwd=cwd, env=clean_environment(), check=True)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def installed_identity() -> dict:
    """Require installed packages rather than development source overlays."""
    repositories = json.loads((PROFILE / "stack.json").read_text(encoding="utf-8"))["repositories"]
    result = {}
    for name, module_name in MODULES.items():
        module = importlib.import_module(module_name)
        path = Path(module.__file__).resolve()
        if not path.is_relative_to(Path(sys.prefix).resolve()):
            raise ValueError(f"{name} is not installed in this environment: {path}")
        source = importlib.metadata.distribution(name).read_text("direct_url.json")
        identity = json.loads(source or "{}")
        commit = identity.get("vcs_info", {}).get("commit_id")
        if identity.get("dir_info", {}).get("editable") or commit != repositories[name]:
            raise ValueError(f"installed source identity mismatch: {name}")
        result[name] = {"module": module_name, "path": str(path), "commit": commit}
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--source-root", type=Path, help="Optional local source repositories")
    parser.add_argument("--verify-installed", action="store_true")
    args = parser.parse_args(argv)
    if args.verify_installed:
        print(json.dumps(installed_identity(), indent=2))
        return
    root = args.root.resolve()
    if root.exists():
        parser.error("--root must not exist; use a new directory and preserve failed installations")
    version = subprocess.check_output(
        [args.python, "-I", "-c", "import sys; print('%s.%s' % sys.version_info[:2])"], text=True
    ).strip()
    if version != "3.12":
        parser.error("this installed integration profile requires Python3.12")
    stack = json.loads((PROFILE / "stack.json").read_text(encoding="utf-8"))
    if stack.get("schema_version") != "quant.durable-research-stack/v1":
        raise ValueError("unsupported source profile")
    repositories = stack["repositories"]
    if set(repositories) != set(MODULES):
        raise ValueError("profile must specify the complete six-package runtime")
    if any(not re.fullmatch(r"[0-9a-f]{40}", revision) for revision in repositories.values()):
        raise ValueError("every source must use a full commit")
    lock = PROFILE / "requirements.lock"
    lock_hash = hashlib.sha256(lock.read_bytes()).hexdigest()
    if lock_hash != stack["requirements_sha256"]:
        raise ValueError("dependency lock identity mismatch")
    root.mkdir(parents=True)
    workspace = root / "workspace"
    workspace.mkdir()
    for name, revision in repositories.items():
        repo = workspace / name
        source = (
            str(args.source_root.resolve() / name)
            if args.source_root
            else f"https://github.com/PureSaber/{name}.git"
        )
        clone_args = ["git", "clone", "-c", "core.autocrlf=false", "--no-checkout"]
        if args.source_root:
            clone_args += ["--local", "--no-hardlinks"]
        call(*clone_args, source, repo)
        call("git", "-C", repo, "checkout", "--detach", revision)
        if git(repo, "rev-parse", "HEAD") != revision or git(repo, "status", "--porcelain"):
            raise ValueError(f"source checkout does not match profile: {name}")
    call(args.python, "-I", "-m", "venv", root / "env")
    python = root / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    call(python, "-I", "-m", "pip", "install", "--no-deps", "-r", lock)
    for name in repositories:
        call(
            python,
            "-I",
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            f"{name} @ git+{(workspace / name).as_uri()}@{repositories[name]}",
        )
    call(python, "-I", "-m", "pip", "check")
    identity = subprocess.check_output(
        [str(python), "-I", str(__file__), "--root", str(root), "--verify-installed"],
        env=clean_environment(),
        text=True,
    )
    receipt = {
        "schema_version": "quant.durable-research-installation/v1",
        "status": "installed_not_yet_accepted",
        "profile": stack,
        "python": str(python),
        "modules": json.loads(identity),
    }
    (root / "installed-stack.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Installed: {python}; run {PROFILE / 'smoke.py'} --root {root}")


if __name__ == "__main__":
    main()
