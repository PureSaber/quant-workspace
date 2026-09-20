"""Fail closed unless public packages and all local application commits are frozen."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path

from packaging.requirements import Requirement


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def verify() -> list[Path]:
    profile = Path(__file__).resolve().parent
    integration_root = profile.parents[2]
    stack = json.loads((profile / "stack.json").read_text(encoding="utf-8"))
    lock = profile / "requirements.lock"
    if hashlib.sha256(lock.read_bytes()).hexdigest() != stack["requirements_sha256"]:
        raise RuntimeError("Public dependency lock differs from the frozen v2 stack")
    for line in lock.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        installed = distribution(requirement.name)
        if requirement.specifier and not requirement.specifier.contains(installed.version):
            raise RuntimeError(f"Installed {requirement.name} does not match the v2 lock")
    akshare = json.loads(distribution("akshare").read_text("direct_url.json") or "{}")
    installed_hash = akshare.get("archive_info", {}).get("hashes", {}).get("sha256")
    if installed_hash != stack["akshare_wheel_sha256"]:
        raise RuntimeError("Installed AKShare wheel does not match the frozen artifact")

    source_paths = []
    for name, item in stack["repositories"].items():
        repo = (integration_root / item["path"]).resolve()
        if not (repo / ".git").is_dir():
            raise RuntimeError(f"Missing local repository: {name}")
        if _git(repo, "rev-parse", "HEAD") != item["commit"]:
            raise RuntimeError(f"{name} is not at the frozen v2 commit")
        if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
            raise RuntimeError(f"{name} has uncommitted files")
        source = repo / item.get("python_path", "src")
        if not source.is_dir():
            raise RuntimeError(f"{name} Python source path is missing")
        source_paths.append(source)
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    print("Daily research v2 local stack verified")
    return source_paths


if __name__ == "__main__":
    verify()
