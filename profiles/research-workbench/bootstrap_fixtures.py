"""Install the certified futures/crypto runtime separately from current research."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from bootstrap import PROFILE, check_checkout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROFILE.parents[2])
    parser.add_argument("--env", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    stack = json.loads((PROFILE / "stack.json").read_text(encoding="utf-8"))
    lock = PROFILE / "fixtures.lock"
    if hashlib.sha256(lock.read_bytes()).hexdigest() != stack["fixtures_requirements_sha256"]:
        raise ValueError("Frozen fixture lock differs from manifest")
    packages = []
    for name in ("quant-futures-spread", "quant-crypto-basis"):
        check_checkout(root / name, stack["repositories"][name]["commit"])
        packages += ["-e", str(root / name)]
    environment = (args.env or root / ".venv-fixtures").resolve()
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
            str(lock),
        ],
        check=True,
    )
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", "--no-build-isolation", *packages],
        check=True,
    )
    subprocess.run([str(python), "-m", "pip", "check"], check=True)
    worker = root / "quant-pipeline/src/quant_pipeline/research_fixture_worker.py"
    for backend in ("futures_fixture", "crypto_fixture"):
        subprocess.run([str(python), "-I", str(worker), "inspect", backend], check=True)


if __name__ == "__main__":
    main()
