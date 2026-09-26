"""Install the exact research stack without modifying existing checkouts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROFILE = Path(__file__).resolve().parent


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def check_checkout(repo: Path, revision: str) -> None:
    if git(repo, "rev-parse", "HEAD") != revision:
        raise ValueError(
            f"{repo.name} must be at {revision}; use a separate clean integration directory"
        )
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError(f"{repo.name} has uncommitted files")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROFILE.parents[2])
    parser.add_argument("--env", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    stack = json.loads((PROFILE / "stack.json").read_text(encoding="utf-8"))
    packages = []
    for name, item in stack["repositories"].items():
        repo = root / name
        if not repo.exists():
            subprocess.run(
                ["git", "clone", f"https://github.com/PureSaber/{name}.git", str(repo)], check=True
            )
            subprocess.run(["git", "checkout", "--detach", item["commit"]], cwd=repo, check=True)
        check_checkout(repo, item["commit"])
        if item.get("python_path") is not None:
            packages += ["-e", str(repo)]
    environment = (args.env or root / ".venv-research").resolve()
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--index-url",
            "https://pypi.org/simple",
            "-r",
            str(PROFILE / "requirements.lock"),
        ],
        check=True,
    )
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            "-e",
            str(PROFILE.parents[1]),
            *packages,
        ],
        check=True,
    )
    subprocess.run([str(python), "-m", "pip", "check"], check=True)
    print(f"Ready: {python} {PROFILE / 'run.py'} --root {root} plan TEMPLATE")


if __name__ == "__main__":
    main()
