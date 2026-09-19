"""One-command daily workflow, using this profile's installed Python interpreter."""

from pathlib import Path
import subprocess
import sys

from verify import verify

if __name__ == "__main__":
    verify()
    raise SystemExit(
        subprocess.call(
            [
                sys.executable,
                "-m",
                "quant_pipeline.daily",
                "--config",
                str(Path(__file__).resolve().with_name("pipeline.yaml")),
                *sys.argv[1:],
            ]
        )
    )
