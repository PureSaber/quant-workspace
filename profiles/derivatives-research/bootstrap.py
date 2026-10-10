"""Install an isolated, pinned research runtime; never modify existing checkouts."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def call(*args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    root = args.root.resolve()
    if root.exists():
        parser.error("--root must be a new directory; existing environments stay frozen")
    version = subprocess.check_output(
        [args.python, "-c", "import sys; print('%s.%s'%sys.version_info[:2])"], text=True
    ).strip()
    if version != "3.12":
        parser.error("use Python 3.12")
    stack = json.loads(Path(__file__).with_name("stack.json").read_text(encoding="utf-8"))
    root.mkdir(parents=True)
    workspace = root / "workspace"
    workspace.mkdir()
    for name, ref in stack["applications"].items():
        repo = workspace / name
        call("git", "clone", f"https://github.com/PureSaber/{name}.git", repo)
        call("git", "-C", repo, "checkout", "--detach", ref)
        head = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        if head != ref:
            raise ValueError(f"source identity mismatch: {name}")
    lock = workspace / "quant-options/requirements.lock"
    if hashlib.sha256(lock.read_bytes()).hexdigest() != stack["lock_sha256"]:
        raise ValueError("dependency lock identity mismatch")
    call(args.python, "-m", "venv", root / "env")
    python = root / "env" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    call(python, "-m", "pip", "install", "--no-deps", "-r", lock)
    for folder in ["quant-options", "quant-futures-spread/international", "quant-studio"]:
        call(
            python, "-m", "pip", "install", "--no-deps", "--no-build-isolation", workspace / folder
        )
    call(python, "-m", "pip", "check")
    environment = {"QUANT_WORKSPACE_ROOT": str(workspace)}
    for kind, variable in [
        ("option", "QUANT_OPTIONS_BUNDLE"),
        ("future", "QUANT_GLOBAL_FUTURES_BUNDLE"),
    ]:
        bundle = root / "datasets" / kind
        call(
            python,
            "-m",
            "quant_data_kit.derivatives.cli",
            "demo",
            "--kind",
            kind,
            "--output",
            bundle,
        )
        environment[variable] = str(bundle)
    profile = {
        "schema_version": "quant-studio.settings/v1",
        "environment": environment,
        "python_by_repo": {name: str(python) for name in ["quant-options", "quant-futures-global"]},
    }
    (root / "studio-settings.json").write_text(
        json.dumps(profile, indent=2) + "\n", encoding="utf-8"
    )
    (root / "installed-stack.json").write_text(json.dumps(stack, indent=2) + "\n", encoding="utf-8")
    print(
        "Installed. Keep existing Studio settings; merge only the two new runtime and dataset entries."
    )


if __name__ == "__main__":
    main()
