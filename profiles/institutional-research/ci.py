"""Fetch exact public sources and validate the packaged research environment."""

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def main():
    here = Path(__file__).resolve().parent
    root = here.parents[2]
    sources = json.loads((here / "sources.lock.json").read_text("utf-8"))
    environment = os.environ.copy()
    token = environment.pop("QUANT_RESEARCH_READ_TOKEN", None)
    fetch_environment = environment.copy()
    if token:
        header = base64.b64encode(("x-access-token:" + token).encode()).decode()
        index = int(fetch_environment.get("GIT_CONFIG_COUNT", "0"))
        fetch_environment["GIT_CONFIG_COUNT"] = str(index + 1)
        fetch_environment[f"GIT_CONFIG_KEY_{index}"] = "http.https://github.com/.extraheader"
        fetch_environment[f"GIT_CONFIG_VALUE_{index}"] = "AUTHORIZATION: basic " + header
    for name, commit in sources.items():
        if not re.fullmatch(r"quant-[a-z-]+", name) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("invalid pinned source identity")
        path = root / name
        if path.exists():
            raise ValueError("dependency checkout must not already exist")
        subprocess.run(["git", "init", "-q", str(path)], check=True, env=environment)
        subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "fetch",
                "--depth",
                "1",
                f"https://github.com/PureSaber/{name}.git",
                commit,
            ],
            check=True,
            env=fetch_environment,
        )
        subprocess.run(
            ["git", "-C", str(path), "checkout", "--detach", "FETCH_HEAD"],
            check=True,
            env=environment,
        )
    runtime = root / ".institutional-runtime"
    subprocess.run(
        [
            sys.executable,
            str(here / "bootstrap.py"),
            "--environment",
            str(runtime),
            "--repos-root",
            str(root),
        ],
        check=True,
        env=environment,
    )
    python = runtime / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(
        [str(python), "-m", "pytest", str(here / "test_acceptance.py"), "-q"],
        check=True,
        env=environment,
    )
    subprocess.run(
        [
            str(python),
            str(here / "run.py"),
            "acceptance",
            "--output",
            str(root / "institutional-acceptance"),
        ],
        check=True,
        env=environment,
    )


if __name__ == "__main__":
    main()
