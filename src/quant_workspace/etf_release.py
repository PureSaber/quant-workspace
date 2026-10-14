"""Adapter for the existing single-venv ETF bootstrap installation and owner acceptance."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from quant_workspace.delivery import _canonical, _now, _sha, _state_lock, _write_atomic
from quant_workspace.runtime_identity import hidden_options

ACCEPTANCE = "quant.etf-runtime-acceptance/v1"
POINTER = "quant.etf-current-runtime/v1"
CHECKS = {"compatibility", "research", "state_compatibility", "frozen_runtime"}


def inputs(row):
    root = Path(row["etf_root"])
    return [
        root / "installed-stack.json",
        root / "profile/stack.json",
        root / "profile/requirements.lock",
        root / "profile/bootstrap.py",
        Path(row["acceptance"]),
    ]


def current(state):
    path = Path(state) / "etf-current.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text("utf-8"))
    if value.get("schema_version") != POINTER or value.get("business_data_changed") is not False:
        raise ValueError("Invalid ETF runtime pointer")
    return value


def verify(row, previous=None):
    root = Path(row["etf_root"]).resolve()
    python = root / "env/Scripts/python.exe"
    if Path(row["python"]).resolve() != python:
        raise ValueError("ETF runtime Python must be its original root/env interpreter")
    acceptance = json.loads(Path(row["acceptance"]).read_text("utf-8"))
    if acceptance.get("schema_version") != ACCEPTANCE or acceptance.get("status") != "accepted":
        raise ValueError("ETF runtime needs explicit owner acceptance")
    if Path(acceptance["runtime_root"]).resolve() != root:
        raise ValueError("ETF acceptance targets a different runtime")
    expected = acceptance["files"]
    for path in inputs(row)[:-1]:
        if expected.get(path.relative_to(root).as_posix()) != _sha(path.read_bytes()):
            raise ValueError("ETF installation/profile changed after acceptance")
    state_sha = acceptance["state_contract_sha256"]
    if not isinstance(state_sha, str) or len(state_sha) != 64:
        raise ValueError("ETF acceptance needs a state contract digest")
    if previous and previous["state_contract_sha256"] != state_sha:
        transition = [previous["state_contract_sha256"], state_sha]
        if transition not in acceptance.get("state_transitions", []):
            raise ValueError("ETF application state transition was not accepted")
    if set(acceptance["checks"]) != CHECKS:
        raise ValueError("ETF acceptance requires compatibility, research, state and frozen checks")
    for check in acceptance["checks"].values():
        evidence = Path(check["evidence"])
        if (
            check.get("returncode") != 0
            or not evidence.is_absolute()
            or _sha(evidence.read_bytes()) != check["sha256"]
        ):
            raise ValueError("ETF owner acceptance evidence failed or changed")
    # Reuse the existing pinned installer verifier. It compares all eleven
    # imported ordinary-install trees, full VCS pins and the receipt in this root.
    completed = subprocess.run(
        [
            str(python),
            "-I",
            "-X",
            "utf8",
            str(root / "profile/bootstrap.py"),
            "--root",
            str(root),
            "--verify-installed",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
        **hidden_options(),
    )
    if completed.returncode:
        raise ValueError("Native ETF verify-installed failed: " + completed.stderr[-3000:])
    actual = json.loads(completed.stdout)
    saved = json.loads((root / "installed-stack.json").read_text("utf-8"))
    if actual != saved:
        raise ValueError("Native ETF installed identity differs from receipt")
    return {
        "installed_stack_sha256": _sha((root / "installed-stack.json").read_bytes()),
        "acceptance_sha256": _sha(Path(row["acceptance"]).read_bytes()),
        "state_contract_sha256": state_sha,
        "runtime_root": str(root),
        "native_verification": actual,
    }


def activate(row, state, expected, action):
    with _state_lock(Path(state)):
        previous = current(state)
        if previous != expected:
            raise ValueError("ETF release pointer changed since preview")
        verified = verify(row, previous)
        pointer = {
            "schema_version": POINTER,
            "action": action,
            "verified_at": _now(),
            "runtime_root": verified["runtime_root"],
            "installed_stack_sha256": verified["installed_stack_sha256"],
            "acceptance_sha256": verified["acceptance_sha256"],
            "state_contract_sha256": verified["state_contract_sha256"],
            "business_data_changed": False,
        }
        _write_atomic(Path(state) / "etf-receipts" / f"{_sha(_canonical(pointer))}.json", pointer)
        _write_atomic(Path(state) / "etf-current.json", pointer)
        return pointer
