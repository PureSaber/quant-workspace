import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from quant_workspace import etf_release
from quant_workspace.delivery import _sha, _write_atomic


def acceptance_for(root, output):
    evidence = output.parent / "acceptance-check.txt"
    evidence.write_text(
        "synthetic fixture evidence: tests verify receipt binding, not market performance"
    )
    record = {
        "schema_version": etf_release.ACCEPTANCE,
        "status": "accepted",
        "runtime_root": str(root),
        "state_contract_sha256": "a" * 64,
        "state_transitions": [],
        "files": {
            p: _sha((root / p).read_bytes())
            for p in (
                "installed-stack.json",
                "profile/stack.json",
                "profile/requirements.lock",
                "profile/bootstrap.py",
            )
        },
        "checks": {
            name: {
                "returncode": 0,
                "evidence": str(evidence),
                "sha256": _sha(evidence.read_bytes()),
            }
            for name in etf_release.CHECKS
        },
    }
    _write_atomic(output, record)
    return {
        "etf_root": str(root),
        "python": str(root / "env/Scripts/python.exe"),
        "acceptance": str(output),
    }, record


@pytest.fixture
def installed(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    (root / "profile").mkdir(parents=True)
    _write_atomic(root / "installed-stack.json", {"schema": "fixture", "repositories": {}})
    for name in ("stack.json", "requirements.lock", "bootstrap.py"):
        (root / "profile" / name).write_text("fixture")
    row, acceptance = acceptance_for(root, tmp_path / "accepted.json")
    monkeypatch.setattr(
        etf_release.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout=(root / "installed-stack.json").read_text(), stderr=""
        ),
    )
    return row, acceptance


def test_native_adapter_cas_and_state_transition(installed, tmp_path):
    row, _ = installed
    state = tmp_path / "state"
    first = etf_release.activate(row, state, None, "activate")
    assert etf_release.current(state) == first
    assert first["business_data_changed"] is False
    with pytest.raises(ValueError, match="changed since preview"):
        etf_release.activate(row, state, None, "rollback")
    assert etf_release.activate(row, state, first, "rollback")["action"] == "rollback"
    with pytest.raises(ValueError, match="transition"):
        etf_release.verify(row, {"state_contract_sha256": "b" * 64})


@pytest.mark.parametrize(
    "damage", ["profile", "evidence", "status", "root", "missing_check", "python"]
)
def test_native_adapter_rejects_unaccepted_or_changed_runtime(installed, damage):
    row, acceptance = installed
    if damage == "profile":
        (Path(row["etf_root"]) / "profile/stack.json").write_text("changed")
    elif damage == "evidence":
        Path(acceptance["checks"]["research"]["evidence"]).write_text("changed")
    elif damage == "status":
        acceptance["status"] = "failed"
    elif damage == "root":
        acceptance["runtime_root"] += "/elsewhere"
    elif damage == "missing_check":
        del acceptance["checks"]["frozen_runtime"]
    else:
        row["python"] += ".different"
    _write_atomic(Path(row["acceptance"]), acceptance)
    with pytest.raises(ValueError):
        etf_release.verify(row)


def test_native_verifier_failure_is_not_accepted(installed, monkeypatch):
    row, _ = installed
    monkeypatch.setattr(
        etf_release.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=2, stdout="", stderr="import tree mismatch"),
    )
    with pytest.raises(ValueError, match="Native ETF"):
        etf_release.verify(row)


@pytest.mark.skipif(
    not os.environ.get("QUANT_TEST_LEGACY_ROOT"), reason="Needs isolated ordinary ETF install"
)
def test_real_legacy_etf_installed_identity_without_new_control_module(tmp_path):
    root = Path(os.environ["QUANT_TEST_LEGACY_ROOT"])
    row, _ = acceptance_for(root, tmp_path / "accepted.json")
    from quant_workspace.runtime_identity import hidden_options

    result = subprocess.run(
        [
            row["python"],
            "-I",
            "-c",
            "import importlib.util; assert importlib.util.find_spec('quant_workspace.studio_host') is None",
        ],
        check=False,
        capture_output=True,
        **hidden_options(),
    )
    assert result.returncode == 0
    actual = etf_release.verify(row)
    assert len(actual["native_verification"]["repositories"]) == 11
