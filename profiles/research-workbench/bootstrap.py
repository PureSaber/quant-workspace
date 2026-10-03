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


def create_environment(environment: Path, interpreter: str) -> Path:
    """Select the base runtime explicitly and never repurpose an existing environment."""
    identity_code = (
        "import json,sys; from pathlib import Path; "
        "print(json.dumps({'base': str(Path(sys.base_prefix).resolve()), "
        "'version': list(sys.version_info[:3])}))"
    )

    def identity(executable: str) -> dict:
        return json.loads(subprocess.check_output([executable, "-c", identity_code], text=True))

    expected = identity(interpreter)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if python.exists():
        actual = identity(str(python))
        if actual != expected:
            raise ValueError(
                "existing environment uses a different base Python; "
                "choose a new --env directory instead of replacing a frozen runtime"
            )
    else:
        subprocess.run([interpreter, "-m", "venv", str(environment)], check=True)
    return python


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROFILE.parents[2])
    parser.add_argument("--env", type=Path)
    parser.add_argument(
        "--python", default=sys.executable, help="Base Python executable for the new environment"
    )
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
    python = create_environment(environment, args.python)
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
