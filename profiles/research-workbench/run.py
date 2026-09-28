"""Verified entry point for recipes, history, experiments, drafts and reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path

from bootstrap import PROFILE, check_checkout, git
from packaging.requirements import Requirement

COMMANDS = {
    "run": ("quant_pipeline.research_workbench",),
    "demo": ("quant_pipeline.research_demo",),
    "init": ("quant_lab.research", "init"),
    "plan": ("quant_lab.research", "plan"),
    "propose": ("quant_agent.research_assistant",),
    "history": ("quant_data_kit.research_coverage",),
    "report": ("quant_report_hub.research_workbench",),
    "dataset": ("quant_data_kit.research_dataset",),
    "web": ("quant_pipeline.research_web",),
    "paper": ("quant_pipeline.research_paper",),
    "advice": ("quant_agent.research_history",),
}


def verify(root: Path) -> list[Path]:
    stack = json.loads((PROFILE / "stack.json").read_text(encoding="utf-8"))
    lock = PROFILE / "requirements.lock"
    if hashlib.sha256(lock.read_bytes()).hexdigest() != stack["requirements_sha256"]:
        raise ValueError("Research dependency lock differs from manifest")
    for line in lock.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        installed = distribution(requirement.name)
        if requirement.specifier and not requirement.specifier.contains(installed.version):
            raise ValueError(f"Installed {requirement.name} differs from research lock")
    wheel = json.loads(distribution("akshare").read_text("direct_url.json") or "{}")
    if (
        wheel.get("archive_info", {}).get("hashes", {}).get("sha256")
        != stack["akshare_wheel_sha256"]
    ):
        raise ValueError("AKShare artifact differs from research lock")
    paths = []
    for name, item in stack["repositories"].items():
        repo = (root / name).resolve()
        check_checkout(repo, item["commit"])
        if item.get("python_path") is not None:
            source = repo / item["python_path"]
            if not source.is_dir():
                raise ValueError(f"Missing source path: {source}")
            paths.append(source)
    workspace = PROFILE.parents[1]
    if git(workspace, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Workspace profile must be committed")
    paths.append(workspace / "src")
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROFILE.parents[2])
    parser.add_argument("command", choices=[*COMMANDS, "verify"])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    paths = verify(args.root.resolve())
    if args.command == "verify":
        print("Research stack verified")
        return 0
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(str(p) for p in paths)
    return subprocess.call(
        [sys.executable, "-m", *COMMANDS[args.command], *args.arguments], env=environment
    )


if __name__ == "__main__":
    raise SystemExit(main())
