"""Least-privilege Windows task definitions and read-only identity verification."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
ET.register_namespace("", NS)


def task_xml(spec, user_sid, *, enabled=False, probe_at=None):
    if not re.fullmatch(r"S-1-[0-9-]+", user_sid):
        raise ValueError("A Windows user SID is required")
    root = ET.Element(f"{{{NS}}}Task", version="1.2")

    def add(parent, name, text=None, **attrs):
        child = ET.SubElement(parent, f"{{{NS}}}{name}", **attrs)
        child.text = text
        return child

    info = add(root, "RegistrationInfo")
    add(
        info,
        "Description",
        "Quant ETF operations: original account and native timing guards retained",
    )
    triggers = add(root, "Triggers")
    slots = spec.get("slots", [])
    if probe_at:
        trigger = add(triggers, "TimeTrigger")
        add(trigger, "StartBoundary", probe_at.isoformat())
        add(trigger, "Enabled", "true")
    else:
        for slot in slots:
            trigger = add(triggers, "CalendarTrigger")
            add(trigger, "StartBoundary", f"2026-10-16T{slot}:00+08:00")
            add(trigger, "Enabled", "true")
            add(add(trigger, "ScheduleByDay"), "DaysInterval", "1")
        trigger = add(triggers, "LogonTrigger")
        add(trigger, "Enabled", "true")
        add(trigger, "UserId", user_sid)
    principal = add(add(root, "Principals"), "Principal", id="Author")
    add(principal, "UserId", user_sid)
    add(principal, "LogonType", "InteractiveToken")
    add(principal, "RunLevel", "LeastPrivilege")
    settings = add(root, "Settings")
    for key, text in {
        "MultipleInstancesPolicy": "IgnoreNew",
        "DisallowStartIfOnBatteries": "false",
        "StopIfGoingOnBatteries": "false",
        "AllowHardTerminate": "false",
        "StartWhenAvailable": "false",
        "RunOnlyIfNetworkAvailable": "false",
        "Enabled": str(enabled).lower(),
        "Hidden": "true",
        "WakeToRun": "false",
        "ExecutionTimeLimit": "PT0S",
    }.items():
        add(settings, key, text)
    execute = add(add(root, "Actions", Context="Author"), "Exec")
    add(execute, "Command", spec["execute"])
    add(execute, "Arguments", spec["arguments"])
    add(execute, "WorkingDirectory", spec["working_directory"])
    return ET.tostring(root, encoding="unicode")


def parse_task(xml):
    root = ET.fromstring(xml)
    ns = {"t": NS}

    def text(path):
        return root.findtext(path, namespaces=ns)

    actions = root.findall("t:Actions/t:Exec", ns)
    if len(actions) != 1:
        raise ValueError("Expected exactly one task action")
    starts = [
        element.text for element in root.findall("t:Triggers/t:CalendarTrigger/t:StartBoundary", ns)
    ]
    return {
        "execute": text("t:Actions/t:Exec/t:Command"),
        "arguments": text("t:Actions/t:Exec/t:Arguments") or "",
        "working_directory": text("t:Actions/t:Exec/t:WorkingDirectory"),
        "enabled": text("t:Settings/t:Enabled") == "true",
        "slots": sorted(datetime.fromisoformat(x).strftime("%H:%M") for x in starts),
        "logon_type": text("t:Principals/t:Principal/t:LogonType"),
        "run_level": text("t:Principals/t:Principal/t:RunLevel"),
        "user_sid": text("t:Principals/t:Principal/t:UserId"),
        "logon_sid": text("t:Triggers/t:LogonTrigger/t:UserId"),
        "beijing_slots": all(
            datetime.fromisoformat(x).utcoffset() == timedelta(hours=8) for x in starts
        ),
        "multiple_instances": text("t:Settings/t:MultipleInstancesPolicy"),
        "wake": text("t:Settings/t:WakeToRun"),
        "catchup": text("t:Settings/t:StartWhenAvailable"),
        "logon_trigger": len(root.findall("t:Triggers/t:LogonTrigger", ns)) == 1,
        "daily_intervals": [
            e.text
            for e in root.findall("t:Triggers/t:CalendarTrigger/t:ScheduleByDay/t:DaysInterval", ns)
        ],
        "all_triggers_enabled": all(
            e.findtext("t:Enabled", namespaces=ns) == "true" for e in root.find("t:Triggers", ns)
        ),
        "trigger_count": len(root.find("t:Triggers", ns)),
        "hard_terminate": text("t:Settings/t:AllowHardTerminate"),
    }


def inspect_task(spec):
    if os.name != "nt":
        return {"verified": False, "enabled": False, "reason": "Windows task unavailable"}
    name = spec["name"]
    if not re.fullmatch(r"Quant-[A-Za-z0-9-]+", name):
        raise ValueError("Invalid managed task name")
    command = (
        "$ErrorActionPreference='Stop'; "
        f"$t=Get-ScheduledTask -TaskName '{name}' -TaskPath '\\'; "
        "$i=$t | Get-ScheduledTaskInfo; "
        "@{xml=($t | Export-ScheduledTask);state=[string]$t.State;"
        "user_sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value;"
        "last_run=$i.LastRunTime.ToString('o');last_result=$i.LastTaskResult} | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=True,
        )
        info = json.loads(result.stdout)
        actual = parse_task(info["xml"])
        verified = all(
            actual[key] == spec[key]
            for key in ("execute", "arguments", "working_directory", "slots")
        )
        verified = verified and all(
            actual[key] == expected
            for key, expected in {
                "logon_type": "InteractiveToken",
                "run_level": "LeastPrivilege",
                "multiple_instances": "IgnoreNew",
                "wake": "false",
                "catchup": "false",
                "logon_trigger": True,
                "all_triggers_enabled": True,
                "daily_intervals": ["1"] * len(spec["slots"]),
                "trigger_count": len(spec["slots"]) + 1,
                "hard_terminate": "false",
                "user_sid": info["user_sid"],
                "logon_sid": info["user_sid"],
                "beijing_slots": True,
            }.items()
        )
        return {
            "name": name,
            "verified": verified,
            "enabled": actual["enabled"],
            "state": info["state"],
            "last_run": info["last_run"],
            "last_result": info["last_result"],
            "reason": "" if verified else "Task configuration differs from approved definition",
        }
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {
            "name": name,
            "verified": False,
            "enabled": False,
            "reason": "Task missing or unreadable: " + str(exc),
        }


def start_studio(control_path):
    from quant_workspace.local_control import _healthy, execute, load_config, preview, status

    config = load_config(control_path)
    observed = status(config)
    process = observed.get("observed_process") or {}
    if process.get("alive") and process.get("listener_owned") and _healthy(config["service"]):
        return observed
    plan = preview(control_path, "start")
    # The native preview and execution revalidate identity, ownership and protected inputs.
    return execute(control_path, "start", plan["target"], plan["plan_sha256"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "xml", "start-studio", "probe"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--sid")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "start-studio":
        try:
            result = start_studio(args.config)
        except Exception as exc:
            result = {"state": "failed", "error": str(exc)}
            if args.output:
                args.output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            raise
        if args.output:
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8"
            )
    elif args.command == "probe":
        # Registered temporary task writes only its own evidence, never calls business code.
        result = {
            "schema": "quant.windows-trigger-probe/v1",
            "pid": os.getpid(),
            "actual_time": datetime.now().astimezone().isoformat(),
            "business_data_changed": False,
        }
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    else:
        spec = json.loads(args.config.read_text("utf-8"))["scheduler"]
        if args.command == "status":
            result = inspect_task(spec)
        else:
            args.output.write_text(task_xml(spec, args.sid), encoding="utf-16")
            result = {"xml": str(args.output), "enabled": False}
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
