"""Verify the frozen install closure before opening a paper account."""

from importlib.metadata import distribution
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from packaging.requirements import Requirement


def verify():
    root = Path(__file__).resolve().parent
    stack = json.loads((root / "stack.json").read_text(encoding="utf-8"))
    lock = root / "requirements.lock"
    if hashlib.sha256(lock.read_bytes()).hexdigest() != stack["requirements_sha256"]:
        raise RuntimeError("Install closure differs from the frozen stack")
    for line in lock.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        req = Requirement(line)
        if req.marker and not req.marker.evaluate():
            continue
        dist = distribution(req.name)
        if req.specifier and not req.specifier.contains(dist.version):
            raise RuntimeError(f"Installed {req.name} does not match the lock")
    for name, ref in stack["repositories"].items():
        provenance = json.loads(distribution(name).read_text("direct_url.json") or "{}")
        vcs = provenance.get("vcs_info", {})
        if not (vcs.get("commit_id") == ref or vcs.get("requested_revision") == ref):
            raise RuntimeError(f"{name} is not installed from the frozen VCS reference")
        if provenance.get("dir_info", {}).get("editable"):
            raise RuntimeError(f"Editable package is not an immutable install: {name}")
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    print("Frozen daily stack verified")


if __name__ == "__main__":
    verify()
