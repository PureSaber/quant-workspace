import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from quant_workspace import local_control as control
from quant_workspace.delivery import _write_atomic
from quant_workspace.runtime_identity import hidden_options, probe_python


@pytest.fixture
def configured(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {"schema_version": "quant-studio.settings/v1", "environment": {}, "python_by_repo": {}}
        )
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    config = {
        "schema_version": "quant.local-control/v1",
        "state_dir": str(tmp_path / "control"),
        "release_state_dir": str(tmp_path / "releases"),
        "service": {
            "host": "127.0.0.1",
            "port": port,
            "runs_root": str(tmp_path / "runs"),
            "settings": str(settings),
            "stop_file": str(tmp_path / "stop"),
        },
        "runtimes": {"old": {"python": sys.executable, "cwd": str(tmp_path), "baseline": True}},
        "initial_runtime": "old",
        "activity_roots": [],
        "protected_files": [],
    }
    path = tmp_path / "control.json"
    _write_atomic(path, config)
    return path, config


def mock_identity(monkeypatch):
    identity = {"python": sys.executable, "packages": {}}
    monkeypatch.setattr(control, "_check_target", lambda *_: identity)
    return identity


def test_stale_preview_rejects_changed_settings_and_busy_work(configured, monkeypatch):
    path, config = configured
    mock_identity(monkeypatch)
    plan = control.preview(path, "start")
    Path(config["service"]["settings"]).write_text('{"changed":true}')
    with pytest.raises(ValueError, match="stale"):
        control.execute(path, "start", None, plan["plan_sha256"])
    marker = Path(config["service"]["runs_root"]) / ".local-maintenance.json"
    assert not marker.exists()
    _write_atomic(Path(config["service"]["runs_root"]) / ".jobs/test.json", {"status": "running"})
    with pytest.raises(ValueError, match="Active"):
        control.preview(path, "start")
    with pytest.raises(ValueError, match="Active"), control.maintenance_gate(config):
        pytest.fail("busy gate entered")
    assert not marker.exists()


def test_admission_lock_blocks_other_process_then_marker_blocks_new_submission(configured):
    _, config = configured
    runs = config["service"]["runs_root"]
    script = """
import sys
from pathlib import Path
from quant_workspace.local_control import admission_lock
try:
    with admission_lock(sys.argv[1]):
        assert not (Path(sys.argv[1]) / '.local-maintenance.json').exists(), 'maintenance'
except OSError:
    sys.exit(23)
except AssertionError:
    sys.exit(24)
"""
    with control.admission_lock(runs):
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, runs], check=False, **hidden_options()
        )
        assert result.returncode == 23
        with pytest.raises(OSError), control.maintenance_gate(config):
            pytest.fail("contended gate entered")
    with control.maintenance_gate(config):
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, runs], check=False, **hidden_options()
        )
        assert result.returncode == 24
    assert not (Path(runs) / ".local-maintenance.json").exists()


def test_failed_switch_recovers_old_runtime_and_keeps_new_ledger(configured, monkeypatch):
    path, config = configured
    config["runtimes"]["new"] = {
        **config["runtimes"]["old"],
        "candidate": str(path),
        "evidence": str(path),
        "prepared_root": str(path.parent / "new"),
    }
    _write_atomic(path, config)
    state = Path(config["state_dir"])
    _write_atomic(state / "process.json", {"pid": 42, "process_token": "old-token"})
    ledger = path.parent / "account.db"
    ledger.write_bytes(b"new financial observations must survive rollback")
    mock_identity(monkeypatch)
    monkeypatch.setattr(control, "process_token", lambda _: "old-token")
    monkeypatch.setattr(control, "_stop", lambda _: None)
    calls = []

    def start(_, target, identity):
        calls.append(target)
        if target == "new":
            raise ValueError("candidate listener failed")

    monkeypatch.setattr(control, "_start", start)
    plan = control.preview(path, "activate", "new")
    with pytest.raises(ValueError, match="listener failed"):
        control.execute(path, "activate", "new", plan["plan_sha256"])
    assert calls == ["new", "old"]
    receipt = json.loads((state / "last-operation.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["recovery"]["status"] == "restored_previous_runtime"
    assert not (state / "runtime.json").exists()
    assert ledger.read_bytes() == b"new financial observations must survive rollback"


def test_successful_switch_commits_pointer_only_after_listener_verification(
    configured, monkeypatch
):
    path, config = configured
    config["runtimes"]["new"] = {
        **config["runtimes"]["old"],
        "candidate": str(path),
        "evidence": str(path),
        "prepared_root": str(path.parent / "new"),
    }
    _write_atomic(path, config)
    mock_identity(monkeypatch)
    calls = []
    monkeypatch.setattr(control, "_stop", lambda _: calls.append("stop"))
    monkeypatch.setattr(control, "_start", lambda *_: calls.append("verified"))

    def activate(*args, **kwargs):
        assert calls == ["stop", "verified"]
        calls.append("pointer")
        return {"candidate_sha256": "new"}

    monkeypatch.setattr(control, "activate_candidate", activate)
    plan = control.preview(path, "rollback", "new")
    receipt = control.execute(path, "rollback", "new", plan["plan_sha256"])
    assert receipt["status"] == "succeeded"
    assert receipt["business_data_changed"] is False
    assert control._selected(config) == "new"


def test_stale_pid_and_unmanaged_listener_are_never_stopped(configured, monkeypatch):
    _, config = configured
    monkeypatch.setattr(control, "listener_pids", lambda _: {987})
    with pytest.raises(ValueError, match="Unmanaged"):
        control._stop(config)
    _write_atomic(Path(config["state_dir"]) / "process.json", {"pid": 42, "process_token": "old"})
    monkeypatch.setattr(control, "process_token", lambda _: "reused")
    with pytest.raises(ValueError, match="Stale PID"):
        control._stop(config)
    assert not Path(config["service"]["stop_file"]).exists()


def test_status_labels_broken_ancestry_and_recorded_pointer(configured, monkeypatch):
    _, config = configured
    _write_atomic(
        Path(config["state_dir"]) / "process.json",
        {"pid": 42, "process_token": "valid", "launcher_pid": 40, "launcher_token": "valid"},
    )
    monkeypatch.setattr(control, "process_token", lambda _: "valid")
    monkeypatch.setattr(control, "listener_pids", lambda _: {42})
    monkeypatch.setattr(
        control, "process_ancestry", lambda *_: (_ for _ in ()).throw(ValueError("bad chain"))
    )
    result = control.status(config)
    assert result["observed_process"]["alive"]
    assert not result["observed_process"]["listener_owned"]
    assert result["release_pointer"] is None


def test_failed_start_without_creation_token_is_not_alive(configured, monkeypatch):
    path, config = configured
    _write_atomic(Path(config["state_dir"]) / "process.json", {"pid": 42, "process_token": None})
    monkeypatch.setattr(control, "process_token", lambda _: None)
    monkeypatch.setattr(control, "listener_pids", lambda _: set())
    assert not control.status(config)["observed_process"]["alive"]
    mock_identity(monkeypatch)
    monkeypatch.setattr(control, "_start", lambda *_: None)
    plan = control.preview(path, "start")
    assert control.execute(path, "start", None, plan["plan_sha256"])["status"] == "succeeded"


def test_target_settings_are_validated_before_service_stop(configured):
    pytest.importorskip("quant_studio")
    path, config = configured
    _write_atomic(Path(config["service"]["settings"]), {"unknown-schema": True})
    with pytest.raises(ValueError, match="Target settings are incompatible"):
        control.preview(path, "restart")
    assert not Path(config["service"]["stop_file"]).exists()


@pytest.mark.skipif(
    os.name != "nt" or importlib.util.find_spec("quant_studio") is None,
    reason="Windows native acceptance requires installed Studio",
)
def test_real_windows_listener_parent_chain_start_restart_stop_and_imports(configured):
    path, config = configured
    try:
        for action in ("start", "restart"):
            plan = control.preview(path, action)
            receipt = control.execute(path, action, None, plan["plan_sha256"])
            assert receipt["status"] == "succeeded"
            observed = control.status(config)
            record = observed["recorded_process"]
            assert observed["observed_process"]["listener_owned"]
            assert record["parent_chain"][0] == record["pid"]
            assert record["parent_chain"][-1] == record["launcher_pid"]
            assert record["packages"] == probe_python(sys.executable, ("quant-studio",))["packages"]
        plan = control.preview(path, "stop")
        assert control.execute(path, "stop", None, plan["plan_sha256"])["status"] == "succeeded"
        assert not control.status(config)["observed_process"]["alive"]
    finally:
        control._stop(config)


@pytest.mark.skipif(os.name != "nt", reason="Windows owner table")
def test_real_occupied_port_refused(configured):
    _, config = configured
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", config["service"]["port"]))
        listener.listen()
        assert os.getpid() in control.listener_pids(config["service"]["port"])
        with pytest.raises(ValueError, match="occupied"):
            control._start(config, "old", {})


@pytest.mark.skipif(
    os.name != "nt" or importlib.util.find_spec("quant_studio") is None,
    reason="Windows native acceptance requires installed Studio",
)
def test_real_failed_import_verification_restores_previous_listener(configured, monkeypatch):
    path, config = configured
    if os.environ.get("QUANT_TEST_LEGACY_ROOT"):
        legacy = Path(os.environ["QUANT_TEST_LEGACY_ROOT"])
        config["runtimes"]["old"].update(
            python=str(legacy / "env/Scripts/python.exe"), cwd=str(legacy)
        )
    config["runtimes"]["candidate"] = {
        **config["runtimes"]["old"],
        "python": sys.executable,
        "candidate": str(path),
        "evidence": str(path),
        "prepared_root": str(path.parent),
    }
    _write_atomic(path, config)
    ledger = path.parent / "newer-account-ledger.json"
    ledger.write_text('{"latest":20261015}')
    plan = control.preview(path, "start")
    control.execute(path, "start", None, plan["plan_sha256"])
    old_pid = control.status(config)["recorded_process"]["pid"]
    original_check = control._check_target

    def drifted_import(config, target, action):
        # Fault injection after accepted-runtime validation: the actual listener
        # must reject a different imported package and restore a real old service.
        identity = original_check(config, "old", "start")
        if target == "candidate":
            identity = probe_python(sys.executable, ("quant-studio",))
            identity.pop("pid", None)
            identity["packages"]["quant-studio"]["source_tree_sha256"] = "0" * 64
        return identity

    monkeypatch.setattr(control, "_check_target", drifted_import)
    try:
        plan = control.preview(path, "activate", "candidate")
        with pytest.raises(ValueError, match="imported packages differ"):
            control.execute(path, "activate", "candidate", plan["plan_sha256"])
        state = control.status(config)
        assert state["observed_process"]["listener_owned"]
        assert state["recorded_process"]["runtime"] == "old"
        assert state["recorded_process"]["pid"] != old_pid
        assert state["last_operation"]["recovery"]["status"] == "restored_previous_runtime"
        assert ledger.read_text() == '{"latest":20261015}'
    finally:
        control._stop(config)


def test_runtime_record_failure_restores_committed_pointer(configured, monkeypatch):
    path, config = configured
    config["runtimes"]["new"] = {
        **config["runtimes"]["old"],
        "candidate": str(path),
        "evidence": str(path),
        "prepared_root": str(path.parent),
    }
    _write_atomic(path, config)
    mock_identity(monkeypatch)
    monkeypatch.setattr(control, "_stop", lambda _: None)
    monkeypatch.setattr(control, "_start", lambda *_: None)
    pointer = Path(config["release_state_dir"]) / "current.json"

    def commit(*args, **kwargs):
        _write_atomic(pointer, {"fixture": "new-pointer"})
        return {"fixture": "new-pointer"}

    def fail_selected(path, value):
        if path.name == "runtime.json":
            raise OSError("cannot publish selected runtime")
        _write_atomic(path, value)

    monkeypatch.setattr(control, "activate_candidate", commit)
    monkeypatch.setattr(control, "_write_atomic", fail_selected)
    plan = control.preview(path, "activate", "new")
    with pytest.raises(OSError, match="cannot publish"):
        control.execute(path, "activate", "new", plan["plan_sha256"])
    assert not pointer.exists()
    receipt = json.loads((Path(config["state_dir"]) / "last-operation.json").read_text())
    assert receipt["pointer_recovery"]["status"] == "restored_previous_pointer"
    assert receipt["observed_pointer_after"] is None


def test_stdlib_shim_identity_matches_external_probe():
    from quant_workspace.runtime_identity import current_identity as library_identity
    from quant_workspace.studio_host import current_identity

    expected = probe_python(sys.executable, ("quant-workspace",))["packages"]
    actual = current_identity(("quant-workspace",))["packages"]
    assert library_identity(("quant-workspace",))["packages"] == actual
    # pytest may import the reviewed checkout while -I imports its identical
    # ordinary installation. Verify content/algorithm without equating paths.
    for name in expected:
        assert {k: v for k, v in actual[name].items() if k != "source"} == {
            k: v for k, v in expected[name].items() if k != "source"
        }


def test_admission_guard_keeps_original_queue_contract(configured, monkeypatch):
    pytest.importorskip("quant_studio")
    from quant_studio import QuantStudioError, jobs

    from quant_workspace.studio_host import install_admission_guard

    monkeypatch.setattr(jobs.JobManager, "submit", jobs.JobManager.submit)
    expected = "native" if hasattr(jobs, "_admission_lock") else "legacy-wrapper"
    assert install_admission_guard() == expected
    _, config = configured
    manager = jobs.JobManager(config["service"]["runs_root"], start_worker=False)
    try:
        with control.maintenance_gate(config):
            with pytest.raises(QuantStudioError, match="维护"):
                manager.submit("check", "sample", {}, profile=None)
            assert manager.list() == []
        assert manager.submit("check", "sample", {}, profile=None)["status"] == "queued"
    finally:
        manager.close()


@pytest.mark.skipif(
    not os.environ.get("QUANT_TEST_LEGACY_ROOT"), reason="Needs isolated old Studio install"
)
def test_old_runtime_uses_its_settings_and_explicit_daily_environment(configured, monkeypatch):
    path, config = configured
    legacy = Path(os.environ["QUANT_TEST_LEGACY_ROOT"])
    legacy_python = str(legacy / "env/Scripts/python.exe")
    old_settings = config["service"]["settings"]
    settings = json.loads(Path(old_settings).read_text())
    settings["python_by_repo"]["quant-pipeline-frozen"] = legacy_python
    _write_atomic(Path(old_settings), settings)
    new_settings = path.parent / "new-settings.json"
    _write_atomic(
        new_settings, {**settings, "environment": {"QUANT_STUDIO_REGISTRATION": str(path)}}
    )
    script = """
import json, sys
from quant_studio import QuantStudioError
from quant_studio.setup import validate_shape
try:
    validate_shape(json.load(open(sys.argv[1])))
except QuantStudioError:
    sys.exit(17)
"""
    rejected = subprocess.run(
        [legacy_python, "-I", "-c", script, str(new_settings)],
        check=False,
        capture_output=True,
        **hidden_options(),
    )
    assert rejected.returncode == 17, rejected.stderr
    # Exercise the actual immutable old JobManager, rather than deleting a
    # helper still referenced by the new JobManager implementation.
    guard_script = """
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('reviewed_host', sys.argv[1])
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
assert host.install_admission_guard() == 'legacy-wrapper'
from quant_studio import QuantStudioError
from quant_studio.jobs import JobManager
root = Path(sys.argv[2])
manager = JobManager(root, start_worker=False)
try:
    marker = root / '.local-maintenance.json'
    marker.write_text('{}')
    try:
        manager.submit('check', 'sample', {}, profile=None)
    except QuantStudioError:
        pass
    else:
        raise AssertionError('Legacy admission allowed maintenance work')
    assert manager.list() == []
    marker.unlink()
    assert manager.submit('check', 'sample', {}, profile=None)['status'] == 'queued'
finally:
    manager.close()
"""
    guarded = subprocess.run(
        [
            legacy_python,
            "-I",
            "-c",
            guard_script,
            str(Path(control.__file__).with_name("studio_host.py")),
            str(path.parent / "legacy-guard"),
        ],
        capture_output=True,
        check=False,
        **hidden_options(),
    )
    assert guarded.returncode == 0, guarded.stderr
    daily = path.parent / "daily.json"
    daily.write_text("{}")
    config["service"].update(settings=str(new_settings), daily_profile=str(daily))
    config["runtimes"]["old"].update(python=legacy_python, settings=old_settings, cwd=str(legacy))
    _write_atomic(path, config)
    monkeypatch.setenv("QUANT_STUDIO_ETF_DAILY", "wrong-inherited-value")
    plan = control.preview(path, "start")
    assert (
        str(daily) in plan["files"]
        and old_settings in plan["files"]
        and str(new_settings) in plan["files"]
    )
    try:
        control.execute(path, "start", None, plan["plan_sha256"])
        record = control.status(config)["recorded_process"]
        assert record["startup_daily_profile"] == str(daily)
        assert record["admission_protocol"] == "legacy-wrapper"
        assert record["packages"]["quant-studio"]["source"].startswith(str(legacy))
        # The old native configuration reader uses this explicit environment and
        # the frozen interpreter from its own compatible settings file.
        code = """
import json, sys
from quant_studio.settings import use_settings
from quant_studio.etf_daily import configuration
with use_settings(json.load(open(sys.argv[1]))):
    print(json.dumps([str(x) for x in configuration()]))
"""
        actual = subprocess.run(
            [legacy_python, "-I", "-c", code, old_settings],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "QUANT_STUDIO_ETF_DAILY": str(daily)},
            **hidden_options(),
        )
        assert json.loads(actual.stdout) == [str(daily), legacy_python]
    finally:
        control._stop(config)


def test_control_configuration_and_cli_fail_closed(configured, capsys):
    path, config = configured
    assert control.main(["--config", str(path), "start", "--execute"]) == 2
    assert "reviewed plan" in capsys.readouterr().out
    config["service"]["host"] = "0.0.0.0"
    _write_atomic(path, config)
    with pytest.raises(ValueError, match="loopback"):
        control.load_config(path)


def test_switch_refuses_silent_frozen_interpreter_rebinding(configured, monkeypatch):
    path, config = configured
    mock_identity(monkeypatch)
    different = path.parent / "different-settings.json"
    _write_atomic(different, {"python_by_repo": {"quant-pipeline-frozen": sys.executable}})
    config["runtimes"]["old"]["settings"] = str(different)
    _write_atomic(path, config)
    with pytest.raises(ValueError, match="Frozen interpreter binding"):
        control.preview(path, "start")
