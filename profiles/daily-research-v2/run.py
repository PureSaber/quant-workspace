"""Run the clean, commit-pinned local daily-research-v2 stack."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from verify import verify

if __name__ == "__main__":
    source_paths = verify()
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        [*(str(path) for path in source_paths), *([existing] if existing else [])]
    )
    raise SystemExit(
        subprocess.call(
            [
                sys.executable,
                "-m",
                "quant_pipeline.daily",
                "--config",
                str(Path(__file__).resolve().with_name("pipeline.yaml")),
                *sys.argv[1:],
            ],
            env=environment,
        )
    )
