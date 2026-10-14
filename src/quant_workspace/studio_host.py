"""Fixed Studio entrypoint with per-start import attestation; no scheduler."""

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path


def current_identity(packages=("quant-studio",)):
    """This file must run unchanged under old runtimes with no new Workspace module."""
    result = {
        "pid": os.getpid(),
        "python": sys.executable,
        "python_version": sys.version,
        "packages": {},
    }
    for name in packages:
        module = importlib.import_module(name.replace("-", "_"))
        distribution = importlib.metadata.distribution(name)
        direct = distribution.read_text("direct_url.json")
        tree = hashlib.sha256()
        for source in sorted(Path(module.__file__).parent.rglob("*.py")):
            tree.update(source.relative_to(Path(module.__file__).parent).as_posix().encode())
            tree.update(source.read_bytes())
        result["packages"][name] = {
            "version": distribution.version,
            "source": module.__file__,
            "sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
            "source_tree_sha256": tree.hexdigest(),
            "installation": json.loads(direct) if direct else None,
        }
    return result


def install_admission_guard():
    """Old immutable installs need the same admission protocol during managed startup."""
    from quant_studio import QuantStudioError, jobs

    if hasattr(jobs, "_admission_lock"):
        return "native"
    original = jobs.JobManager.submit

    def submit(manager, *args, **kwargs):
        lock = jobs._acquire_root_lock(manager.runs_root / ".local-admission.lock")
        try:
            if (manager.runs_root / ".local-maintenance.json").exists():
                raise QuantStudioError("本机维护期间暂停新任务，请稍后重试")
            return original(manager, *args, **kwargs)
        finally:
            jobs._release_root_lock(lock)

    jobs.JobManager.submit = submit
    return "legacy-wrapper"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--launch", required=True)
    args = parser.parse_args()
    launch = json.loads(Path(args.launch).read_text("utf-8"))
    # Run the same fixed probe inside the actual listener process, not a helper.
    identity = current_identity()
    identity.update(nonce=launch["nonce"], runtime=launch["runtime"], pid=os.getpid())
    identity["startup_daily_profile"] = os.environ.get("QUANT_STUDIO_ETF_DAILY")
    identity["admission_protocol"] = install_admission_guard()
    output = Path(launch["attestation"])
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(identity), encoding="utf-8")
    temporary.replace(output)
    from quant_studio.__main__ import main as studio_main

    return studio_main(launch["args"])


if __name__ == "__main__":
    raise SystemExit(main())
