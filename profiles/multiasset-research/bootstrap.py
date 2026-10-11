"""Reproduce a reviewed research stack without touching existing environments."""

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path


def call(*args):
    subprocess.run([str(a) for a in args], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--archives", type=Path, help="Verified source archive directory; avoids GitHub access"
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if root.exists():
        parser.error("--root must not already exist")
    version = subprocess.check_output(
        [args.python, "-c", "import sys; print('%s.%s'%sys.version_info[:2])"], text=True
    ).strip()
    if version != "3.12":
        parser.error("Python 3.12 is required")
    stack = json.loads(Path(__file__).with_name("stack.json").read_text(encoding="utf-8"))
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    for name, commit in stack["repositories"].items():
        path = workspace / name
        if args.archives:
            entry = stack["source_archives"][name]
            archive = args.archives / entry["file"]
            if hashlib.sha256(archive.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError(f"source archive checksum mismatch: {name}")
            with zipfile.ZipFile(archive) as handle:
                for member in handle.infolist():
                    target = (path / member.filename).resolve()
                    if (
                        not target.is_relative_to(path.resolve())
                        or (member.external_attr >> 16) & 0o170000 == 0o120000
                    ):
                        raise ValueError("unsafe source archive member")
                handle.extractall(path)
        else:
            call(
                "git",
                "clone",
                "-c",
                "core.autocrlf=false",
                f"https://github.com/PureSaber/{name}.git",
                path,
            )
            call("git", "-C", path, "checkout", "--detach", commit)
            actual = subprocess.check_output(
                ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
            ).strip()
            if actual != commit:
                raise ValueError(f"source revision mismatch: {name}")
    lock = Path(__file__).with_name("requirements.lock")
    if hashlib.sha256(lock.read_bytes()).hexdigest() != stack["lock"]["sha256"]:
        raise ValueError("dependency lock checksum mismatch")
    call(args.python, "-m", "venv", root / "env")
    python = root / "env" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    call(python, "-m", "pip", "install", "--no-deps", "-r", lock)
    for package in stack["packages"]:
        call(
            python, "-m", "pip", "install", "--no-deps", "--no-build-isolation", workspace / package
        )
    call(python, "-m", "pip", "check")
    profile = {
        "schema_version": "quant-studio.settings/v1",
        "environment": {"QUANT_WORKSPACE_ROOT": str(workspace)},
        "python_by_repo": {key: str(python) for key in stack["runtime_keys"]},
    }
    (root / "studio-settings.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")
    (root / "installed-stack.json").write_text(json.dumps(stack, indent=2), encoding="utf-8")
    print("Research environment installed. Existing service configuration was not changed.")


if __name__ == "__main__":
    main()
