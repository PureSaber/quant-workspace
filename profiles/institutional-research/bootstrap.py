"""Build a separate Python 3.12 environment from pinned external dependencies."""

import argparse
import subprocess
import sys
import venv
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="构建独立机构研究环境")
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--repos-root", type=Path, default=Path(__file__).resolve().parents[3])
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error("use Python 3.12 for the validated dependency set")
    root = args.repos_root.resolve()
    repos = [
        root / name
        for name in (
            "quant-data-kit",
            "quant-execution",
            "quant-lab",
            "quant-factors",
            "quant-us-equity",
            "quant-options",
            "quant-workspace",
            "quant-studio",
        )
    ]
    if any(not (p / "pyproject.toml").is_file() for p in repos):
        parser.error("all eight local source repositories are required")
    environment = args.environment.resolve()
    if environment.exists():
        parser.error("environment must be a new directory")
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "-r",
            str(Path(__file__).with_name("requirements.lock")),
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
            *[str(p) for p in repos],
        ],
        check=True,
    )
    subprocess.run([str(python), "-m", "pip", "check"], check=True)
    print(python)


if __name__ == "__main__":
    main()
