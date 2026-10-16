import json
import subprocess
from types import SimpleNamespace

import pytest

from quant_workspace import etf_scheduler as scheduler


def spec():
    return {
        "execute": "C:\\quant\\pythonw.exe",
        "arguments": "-m quant_pipeline.etf_operations tick",
        "working_directory": "C:\\quant",
        "slots": ["16:30", "17:00", "18:00", "21:00", "22:00"],
    }


def test_task_is_disabled_least_privilege_without_historical_catchup():
    value = scheduler.parse_task(scheduler.task_xml(spec(), "S-1-5-21-1"))
    assert not value["enabled"]
    assert value["slots"] == spec()["slots"]
    assert value["logon_trigger"]
    assert value["catchup"] == value["wake"] == "false"
    assert value["logon_type"] == "InteractiveToken"
    assert value["run_level"] == "LeastPrivilege"
    assert value["multiple_instances"] == "IgnoreNew"


def test_logon_only_service_has_no_daily_business_trigger():
    value = spec()
    value["slots"] = []
    xml = scheduler.task_xml(value, "S-1-5-21-1", enabled=True)
    assert scheduler.parse_task(xml)["slots"] == []
    assert scheduler.parse_task(xml)["enabled"]


def test_inspection_compares_actual_task_and_missing_is_not_disabled(monkeypatch):
    value = {**spec(), "name": "Quant-ETF-Daily"}
    xml = scheduler.task_xml(value, "S-1-5-21-1", enabled=True)
    monkeypatch.setattr(scheduler, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(scheduler.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            stdout=json.dumps(
                {
                    "xml": xml,
                    "user_sid": "S-1-5-21-1",
                    "state": "Ready",
                    "last_run": "2026-10-16T16:30:00+08:00",
                    "last_result": 0,
                }
            )
        ),
    )
    assert scheduler.inspect_task(value)["verified"]
    assert scheduler.inspect_task(value)["enabled"]
    assert not scheduler.inspect_task(value)["natural_trigger"]
    assert not scheduler.inspect_task({**value, "arguments": "different"})["verified"]

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "powershell")

    monkeypatch.setattr(scheduler.subprocess, "run", fail)
    assert not scheduler.inspect_task(value)["verified"]
    assert not scheduler.inspect_task(value)["enabled"]
    with pytest.raises(ValueError, match="Invalid managed task"):
        scheduler.inspect_task({**value, "name": "invalid'command"})


@pytest.mark.parametrize("event_id,natural", [(107, True), (110, False), (119, False)])
def test_natural_credit_requires_time_trigger_not_manual_or_logon(monkeypatch, event_id, natural):
    value = {**spec(), "name": "Quant-ETF-Daily"}
    monkeypatch.setattr(scheduler, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(scheduler.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            stdout=json.dumps(
                {
                    "xml": scheduler.task_xml(value, "S-1-5-21-1", enabled=True),
                    "user_sid": "S-1-5-21-1",
                    "state": "Running",
                    "last_run": "2026-10-16T16:30:00+08:00",
                    "last_result": 0,
                    "trigger_event": {"id": event_id, "record_id": 100},
                }
            )
        ),
    )
    assert scheduler.inspect_task(value)["natural_trigger"] is natural


def test_start_service_delegates_to_native_controller(monkeypatch):
    from quant_workspace import local_control

    config = {"service": {}}
    monkeypatch.setattr(local_control, "load_config", lambda _: config)
    monkeypatch.setattr(
        local_control,
        "status",
        lambda _: {"observed_process": {"alive": True, "listener_owned": True}},
    )
    monkeypatch.setattr(local_control, "_healthy", lambda _: True)
    assert scheduler.start_studio("config")["observed_process"]["alive"]
    monkeypatch.setattr(local_control, "status", lambda _: {"observed_process": None})
    monkeypatch.setattr(
        local_control, "preview", lambda *a: {"target": "reviewed", "plan_sha256": "sha"}
    )
    calls = []
    monkeypatch.setattr(
        local_control, "execute", lambda *args: calls.append(args) or {"state": "started"}
    )
    assert scheduler.start_studio("config")["state"] == "started"
    assert calls == [("config", "start", "reviewed", "sha")]


def test_cli_preserves_startup_failure_and_probe_evidence(tmp_path, monkeypatch):
    import sys

    output = tmp_path / "result.json"
    config = tmp_path / "config.json"
    monkeypatch.setattr(
        sys, "argv", ["scheduler", "probe", "--config", str(config), "--output", str(output)]
    )
    scheduler.main()
    assert json.loads(output.read_text())["business_data_changed"] is False
    monkeypatch.setattr(
        sys, "argv", ["scheduler", "start-studio", "--config", str(config), "--output", str(output)]
    )

    def fail(path):
        raise ValueError("Port is owned by another process")

    monkeypatch.setattr(scheduler, "start_studio", fail)
    with pytest.raises(ValueError, match="Port is owned"):
        scheduler.main()
    assert json.loads(output.read_text())["state"] == "failed"


def test_naive_timezone_is_not_a_beijing_task():
    xml = scheduler.task_xml(spec(), "S-1-5-21-1").replace("+08:00", "")
    assert not scheduler.parse_task(xml)["beijing_slots"]
