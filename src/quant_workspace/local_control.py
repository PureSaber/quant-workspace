"""Reviewed Windows local start/stop/switch transactions independent of Studio UI."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import socket
import ssl
import subprocess
import time
import uuid
from contextlib import contextmanager
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path

from quant_workspace import etf_release
from quant_workspace.delivery import (
    _canonical,
    _now,
    _sha,
    _state_lock,
    _write_atomic,
    activate_candidate,
    current_release,
    load_candidate,
    verify_acceptance,
)
from quant_workspace.runtime_identity import hidden_options, probe_python, validate_settings
from quant_workspace.runtime_readiness import python_path

ACTIONS = {"start", "stop", "restart", "activate", "rollback"}
ACTIVE = {"queued", "running", "cancelling"}


def load_config(path):
    config = json.loads(Path(path).read_text("utf-8-sig"))
    if config.get("schema_version") != "quant.local-control/v1":
        raise ValueError("Unsupported local control configuration")
    for key in ("state_dir", "release_state_dir"):
        _absolute(config[key])
    if Path(config["state_dir"]).resolve() == Path(config["release_state_dir"]).resolve():
        raise ValueError("Controller state and release state must use separate directories")
    service = config["service"]
    if service["host"] not in {"127.0.0.1", "localhost"}:
        raise ValueError("Local controller only supports an IPv4 loopback listener")
    if not isinstance(service["port"], int) or not 1024 <= service["port"] <= 65535:
        raise ValueError("Invalid local listener port")
    for key in ("runs_root", "settings", "stop_file"):
        _absolute(service[key])
    for key in ("access", "cert", "key", "ca", "templates", "daily_profile"):
        if service.get(key):
            _absolute(service[key])
    if bool(service.get("cert")) != bool(service.get("key")):
        raise ValueError("TLS certificate and key must be configured together")
    if service.get("cert") and not service.get("ca"):
        raise ValueError("TLS verification requires the trusted CA")
    for runtime, row in config["runtimes"].items():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", runtime):
            raise ValueError("Invalid named runtime")
        for key in ("python", "cwd"):
            _absolute(row[key])
        if row.get("settings"):
            _absolute(row["settings"])
        release_fields = {"candidate", "evidence", "prepared_root"}
        if "etf_root" in row:
            _absolute(row["etf_root"])
            _absolute(row["acceptance"])
        elif release_fields & row.keys():
            for key in release_fields:
                _absolute(row[key])
        elif row.get("baseline") is not True:
            raise ValueError("Runtime needs accepted candidate or explicit initial baseline")
    if config["initial_runtime"] not in config["runtimes"]:
        raise ValueError("Initial runtime is not configured")
    families = {
        "etf" if "etf_root" in row else "generic"
        for row in config["runtimes"].values()
        if "etf_root" in row or "candidate" in row
    }
    if len(families) > 1:
        raise ValueError("Do not mix generic and ETF state contracts in one controller")
    for configured_path in config.get("activity_roots", []) + config.get("protected_files", []):
        _absolute(configured_path)
    for account in config.get("account_checks", []):
        for key in ("path", "python"):
            _absolute(account[key])
        if account.get("reader", "paper-account") not in {"paper-account", "paper-accounts"}:
            raise ValueError("Account activity check must use a native account reader")
    return config


def _absolute(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError("Control paths must be administrator-configured absolute paths")
    return Path(value)


def process_token(pid):
    """Creation time plus pid prevents a stale receipt from authorizing another process."""
    if os.name != "nt":
        raise ValueError("Process control is supported only on Windows")
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    handle = kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            raise OSError("Cannot inspect managed process creation time")
        return f"{pid}:{times[0].dwHighDateTime}:{times[0].dwLowDateTime}"
    finally:
        kernel.CloseHandle(handle)


def listener_pids(port):
    """Observe Windows TCP owners; HTTP success alone cannot identify the service."""
    if os.name != "nt":
        raise ValueError("Listener ownership verification is Windows-only")
    from ctypes import wintypes

    class Row(ctypes.Structure):
        _fields_ = [
            (k, wintypes.DWORD)
            for k in (
                "state",
                "local_address",
                "local_port",
                "remote_address",
                "remote_port",
                "pid",
            )
        ]

    api = ctypes.WinDLL("iphlpapi").GetExtendedTcpTable
    size = wintypes.DWORD(0)
    api(None, ctypes.byref(size), False, socket.AF_INET, 5, 0)
    buffer = ctypes.create_string_buffer(size.value)
    if api(buffer, ctypes.byref(size), False, socket.AF_INET, 5, 0):
        raise OSError("Cannot inspect TCP owner table")
    count = wintypes.DWORD.from_buffer(buffer).value
    rows = (Row * count).from_buffer(buffer, ctypes.sizeof(wintypes.DWORD))
    return {
        row.pid for row in rows if row.state == 2 and socket.ntohs(row.local_port & 0xFFFF) == port
    }


def process_ancestry(pid, ancestor):
    """Windows venv launchers may own a different pid from the Python listener."""
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD),
            ("heap", ctypes.c_size_t),
            ("module", wintypes.DWORD),
            ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD),
            ("priority", wintypes.LONG),
            ("flags", wintypes.DWORD),
            ("exe", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError("Cannot inspect process ancestry")
    parents = {}
    try:
        row = Entry()
        row.size = ctypes.sizeof(row)
        valid = kernel.Process32FirstW(handle, ctypes.byref(row))
        while valid:
            parents[row.pid] = row.parent
            valid = kernel.Process32NextW(handle, ctypes.byref(row))
    finally:
        kernel.CloseHandle(handle)
    chain = [pid]
    while chain[-1] != ancestor:
        parent = parents.get(chain[-1])
        if not parent or parent in chain:
            raise ValueError("Listener is not a descendant of the launched process")
        chain.append(parent)
    return chain


def check_idle(config):
    roots = {
        Path(config["service"]["runs_root"]),
        *(Path(p) for p in config.get("activity_roots", [])),
    }
    for root in roots:
        for path in root.rglob("*.json"):
            row = json.loads(path.read_text("utf-8-sig"))
            if (
                isinstance(row, dict)
                and (
                    path.parent.name == ".jobs"
                    or path.name == "operation.json"
                    or row.get("schema") == "quant.project-registration/v1"
                )
                and row.get("status", row.get("state")) in ACTIVE | {"registering"}
            ):
                raise ValueError("Active or interrupted work blocks maintenance: " + str(path))
    if config.get("account_checks"):
        from quant_workspace.operational_backup import native_read

        for account in config["account_checks"]:
            native_read(
                {"python": account["python"]},
                {**account, "reader": account.get("reader", "paper-account")},
                Path(account["path"]),
            )


def _protected(config):
    paths = {config["service"]["settings"], *config.get("protected_files", [])}
    settings_paths = {
        config["service"]["settings"],
        *(row["settings"] for row in config["runtimes"].values() if row.get("settings")),
    }
    paths.update(settings_paths)
    frozen = set()
    daily = set()
    for path in settings_paths:
        settings = json.loads(Path(path).read_text("utf-8-sig"))
        python = settings.get("python_by_repo", {}).get("quant-pipeline-frozen")
        frozen.add(str(Path(python).resolve()) if python else None)
        profile = settings.get("environment", {}).get("QUANT_STUDIO_ETF_DAILY")
        if profile:
            daily.add(str(Path(profile).resolve()))
    if len(frozen) > 1:
        raise ValueError("Frozen interpreter binding differs across runtime settings")
    if config["service"].get("daily_profile"):
        paths.add(config["service"]["daily_profile"])
        daily.add(str(Path(config["service"]["daily_profile"]).resolve()))
    if len(daily) > 1:
        raise ValueError("Daily account profile differs across runtime settings")
    paths.update(daily)
    return {p: _sha(Path(p).read_bytes()) for p in sorted(paths)}


def _read(path):
    return json.loads(path.read_text("utf-8")) if path.is_file() else None


def status(config):
    state = Path(config["state_dir"])
    managed = _read(state / "process.json")
    observed = None
    if managed:
        token = process_token(managed["pid"])
        observed = {
            "alive": token is not None and token == managed["process_token"],
            "process_token": token,
            "listener_owned": managed["pid"] in listener_pids(config["service"]["port"]),
        }
        if observed["alive"] and managed.get("launcher_pid"):
            try:
                if process_token(managed["launcher_pid"]) != managed["launcher_token"]:
                    raise ValueError("Recorded launcher creation identity is stale")
                observed["parent_chain"] = process_ancestry(managed["pid"], managed["launcher_pid"])
            except (OSError, ValueError) as exc:
                observed["listener_owned"] = False
                observed["ancestry_error"] = str(exc)
    return {
        "schema_version": "quant.local-control-status/v1",
        "checked_at": _now(),
        "recorded_process": managed,
        "observed_process": observed,
        "selected_runtime": _read(state / "runtime.json"),
        "release_pointer": current_release(Path(config["release_state_dir"])),
        "etf_release_pointer": etf_release.current(config["release_state_dir"]),
        "last_operation": _read(state / "last-operation.json"),
        "scope": "Recorded pointer is not proof of a running process; ownership is observed.",
    }


def _selected(config):
    selected = _read(Path(config["state_dir"]) / "runtime.json")
    return selected["runtime"] if selected else config["initial_runtime"]


def _check_target(config, target, action):
    row = config["runtimes"].get(target)
    if not row:
        raise ValueError("Choose a configured runtime name")
    identity = probe_python(row["python"], ("quant-studio",))
    validate_settings(row["python"], row.get("settings", config["service"]["settings"]))
    if "etf_root" in row:
        identity["etf_verification"] = etf_release.verify(
            row, etf_release.current(config["release_state_dir"])
        )
    elif "candidate" in row:
        candidate, _ = load_candidate(Path(row["candidate"]))
        studio = [p for p in candidate["projects"] if p["distribution"] == "quant-studio"]
        if (
            len(studio) != 1
            or Path(row["python"]).resolve()
            != python_path(
                Path(row["prepared_root"]) / studio[0]["repo"] / studio[0]["environment"]
            ).resolve()
        ):
            raise ValueError("Target interpreter differs from the accepted Studio environment")
        evidence = verify_acceptance(candidate, Path(row["evidence"]), Path(row["prepared_root"]))
        current = current_release(Path(config["release_state_dir"]))
        if current:
            old = current["receipt_record"]["state_contract_sha256"]
            new = candidate["state_contract_sha256"]
            transitions = {
                (c.get("from_state_sha256"), c.get("to_state_sha256"))
                for c in candidate["acceptance_suite"]["cases"]
                if c["kind"] == "state_compatibility"
            }
            if old != new and (old, new) not in transitions:
                raise ValueError("No executed compatibility case for current application state")
        for package in identity["packages"].values():
            if (
                not Path(package["source"])
                .resolve()
                .is_relative_to(Path(row["prepared_root"]).resolve())
            ):
                raise ValueError("Imported package is outside the prepared release")
        identity["acceptance_sha256"] = evidence["acceptance_sha256"]
    elif action in {"activate", "rollback"}:
        raise ValueError("Explicit switch requires an accepted release; baseline is recovery-only")
    identity.pop("pid", None)
    return identity


def preview(config_path, action, target=None):
    if action not in ACTIONS:
        raise ValueError("Unknown control action")
    config = load_config(config_path)
    check_idle(config)
    target = target or _selected(config)
    selected = _selected(config)
    if action in {"start", "stop", "restart"} and target != selected:
        raise ValueError("Use activate/rollback to change the selected runtime")
    identity = _check_target(config, target, action) if action != "stop" else None
    current = current_release(Path(config["release_state_dir"]))
    runtime = config["runtimes"][target]
    files = {
        str(Path(config_path).resolve()): _sha(Path(config_path).read_bytes()),
        **_protected(config),
        str(Path(__file__).with_name("studio_host.py")): _sha(
            Path(__file__).with_name("studio_host.py").read_bytes()
        ),
    }
    for key in ("candidate", "evidence"):
        if key in runtime:
            files[runtime[key]] = _sha(Path(runtime[key]).read_bytes())
    if "etf_root" in runtime:
        for path in etf_release.inputs(runtime):
            files[str(path)] = _sha(path.read_bytes())
    plan = {
        "schema_version": "quant.local-control-plan/v1",
        "action": action,
        "target": target,
        "previous_runtime": selected,
        "files": files,
        "expected_current": current["candidate_sha256"] if current else None,
        "expected_etf_current": etf_release.current(config["release_state_dir"]),
        "process": _read(Path(config["state_dir"]) / "process.json"),
        "imports": identity,
        "business_data_changed": False,
        "frozen_bindings_changed": False,
    }
    return {**plan, "plan_sha256": _sha(_canonical(plan))}


@contextmanager
def admission_lock(runs_root):
    """Shared protocol with Studio submit; marker creation and idle check are atomic."""
    path = Path(runs_root) / ".local-admission.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def maintenance_gate(config):
    path = Path(config["service"]["runs_root"]) / ".local-maintenance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Existing marker is intentionally fail-closed after a controller crash.
    with admission_lock(config["service"]["runs_root"]):
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "created_at": _now()}, stream)
        try:
            check_idle(config)
        except BaseException:
            path.unlink()
            raise
    try:
        yield
    finally:
        path.unlink()


def _healthy(service):
    if service.get("cert"):
        conn = HTTPSConnection(
            service["host"],
            service["port"],
            timeout=1,
            context=ssl.create_default_context(cafile=service["ca"]),
        )
    else:
        conn = HTTPConnection(service["host"], service["port"], timeout=1)
    try:
        conn.request("GET", "/")
        response = conn.getresponse()
        response.read()
        return response.status in {200, 302, 303}
    except OSError:
        return False
    finally:
        conn.close()


def _stop(config):
    state = Path(config["state_dir"])
    record = _read(state / "process.json")
    if not record:
        if listener_pids(config["service"]["port"]):
            raise ValueError("Unmanaged listener: explicit operator handoff is required")
        return
    token = process_token(record["pid"])
    if token is None:
        if listener_pids(config["service"]["port"]):
            raise ValueError("Port belongs to another process")
        return
    if token != record["process_token"]:
        raise ValueError("Stale PID receipt; refusing to stop a different process")
    stop = Path(config["service"]["stop_file"])
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text("local maintenance", encoding="utf-8")
    deadline = time.monotonic() + config.get("stop_timeout", 40)
    while time.monotonic() < deadline:
        if process_token(record["pid"]) != token:
            if listener_pids(config["service"]["port"]):
                raise ValueError("Port was occupied after managed process stopped")
            return
        time.sleep(0.2)
    raise ValueError("Graceful stop timed out; existing process was not forcibly killed")


def _start(config, runtime, expected_identity):
    service = config["service"]
    if listener_pids(service["port"]):
        raise ValueError("Listener port is occupied; no unrelated process will be stopped")
    state = Path(config["state_dir"])
    row = config["runtimes"][runtime]
    stop = Path(service["stop_file"])
    if stop.exists():
        stop.unlink()
    nonce = uuid.uuid4().hex
    attestation = state / f"attestation-{nonce}.json"
    args = ["serve", "--host", service["host"], "--port", str(service["port"])]
    for key, flag in {
        "runs_root": "runs-root",
        "settings": "settings",
        "stop_file": "stop-file",
        "templates": "templates",
        "access": "access",
        "cert": "tls-cert",
        "key": "tls-key",
    }.items():
        if service.get(key):
            args += [
                "--" + flag,
                row.get("settings", service[key]) if key == "settings" else service[key],
            ]
    launch = state / f"launch-{nonce}.json"
    _write_atomic(
        launch, {"nonce": nonce, "runtime": runtime, "attestation": str(attestation), "args": args}
    )
    shim_bytes = Path(__file__).with_name("studio_host.py").read_bytes()
    shim = state / f"studio-host-{_sha(shim_bytes)}.py"
    if shim.exists():
        if shim.read_bytes() != shim_bytes:
            raise ValueError("Standalone startup shim digest mismatch")
    else:
        with shim.open("xb") as stream:
            stream.write(shim_bytes)
    environment = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "PYTHONPATH",
            "PYTHONHOME",
            "QUANT_STUDIO_ETF_DAILY",
            "QUANT_STUDIO_DELIVERY_CONFIG",
            "QUANT_STUDIO_REGISTRATION",
            "QUANT_STUDIO_RUNTIMES",
        }
    }
    if service.get("daily_profile"):
        environment["QUANT_STUDIO_ETF_DAILY"] = service["daily_profile"]
    with (state / f"server-{nonce}.log").open("ab") as log:
        child = subprocess.Popen(
            [
                row["python"],
                "-I",
                "-X",
                "utf8",
                str(shim),
                "--launch",
                str(launch),
            ],
            cwd=row["cwd"],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            **hidden_options(),
        )
    token = process_token(child.pid)
    _write_atomic(
        state / "process.json",
        {
            "pid": child.pid,
            "process_token": token,
            "runtime": runtime,
            "nonce": nonce,
            "started_at": _now(),
            "verified": False,
        },
    )
    try:
        deadline = time.monotonic() + config.get("start_timeout", 30)
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise ValueError(f"Candidate process exited with {child.returncode}")
            observed = _read(attestation)
            if (
                observed
                and observed.get("pid") in listener_pids(service["port"])
                and _healthy(service)
            ):
                if observed.get("nonce") != nonce:
                    raise ValueError("Listener attestation identity mismatch")
                chain = process_ancestry(observed["pid"], child.pid)
                if observed["packages"] != expected_identity["packages"] or (
                    Path(observed["python"]).resolve() != Path(row["python"]).resolve()
                ):
                    raise ValueError("Listener imported packages differ from checked runtime")
                record = {
                    **observed,
                    "process_token": process_token(observed["pid"]),
                    "launcher_pid": child.pid,
                    "launcher_token": token,
                    "parent_chain": chain,
                    "verified": True,
                    "verified_at": _now(),
                }
                _write_atomic(state / "process.json", record)
                return record
            time.sleep(0.2)
        raise ValueError("Candidate listener verification timed out")
    except BaseException:
        # Only this just-created child is eligible for forced cleanup.
        if child.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
                **hidden_options(),
            )
            child.wait(timeout=10)
        raise


def execute(config_path, action, target, expected_plan):
    config = load_config(config_path)
    state = Path(config["state_dir"])
    with _state_lock(state), maintenance_gate(config):
        plan = preview(config_path, action, target)
        if plan["plan_sha256"] != expected_plan:
            raise ValueError("Preview is stale; review a new plan")
        old = plan["previous_runtime"]
        previous_record = _read(state / "process.json")
        was_running = bool(
            previous_record
            and previous_record.get("process_token")
            and process_token(previous_record["pid"]) == previous_record["process_token"]
        )
        if action == "start" and was_running:
            raise ValueError("Managed runtime already runs; use restart")
        old_identity = _check_target(config, old, "start") if was_running else None
        receipt = {
            "schema_version": "quant.local-control-receipt/v1",
            "created_at": _now(),
            "plan": plan,
            "status": "running",
            "recovery": None,
            "business_data_changed": False,
            "frozen_bindings_changed": False,
        }
        _write_atomic(state / "last-operation.json", receipt)
        stopped = False
        started = False
        pointer_path = Path(config["release_state_dir"]) / (
            "etf-current.json"
            if "etf_root" in config["runtimes"][plan["target"]]
            else "current.json"
        )
        pointer_before = _read(pointer_path)
        pointer_committed = None
        pointer_attempted = False
        try:
            if action != "start":
                _stop(config)
                stopped = True
            if action != "stop":
                _start(config, plan["target"], plan["imports"])
                started = True
                if _protected(config) != {p: plan["files"][p] for p in _protected(config)}:
                    raise ValueError("Protected settings/bindings changed during activation")
            if action in {"activate", "rollback"}:
                pointer_attempted = True
                row = config["runtimes"][plan["target"]]
                if "etf_root" in row:
                    receipt["release"] = etf_release.activate(
                        row, config["release_state_dir"], plan["expected_etf_current"], action
                    )
                else:
                    receipt["release"] = activate_candidate(
                        Path(row["candidate"]),
                        Path(row["evidence"]),
                        Path(row["prepared_root"]),
                        Path(config["release_state_dir"]),
                        expected_current=plan["expected_current"],
                        action=action,
                    )
                pointer_committed = _read(pointer_path)
            if action != "stop":
                _write_atomic(
                    state / "runtime.json", {"runtime": plan["target"], "verified_at": _now()}
                )
            receipt["status"] = "succeeded"
        except Exception as exc:
            receipt.update(status="failed", error=str(exc))
            if pointer_attempted and pointer_committed is None:
                receipt["pointer_recovery"] = {
                    "status": "commit_not_confirmed",
                    "action": "Inspect observed_pointer_after and release receipts",
                }
            if pointer_committed is not None:
                try:
                    with _state_lock(Path(config["release_state_dir"])):
                        if _read(pointer_path) != pointer_committed:
                            raise ValueError(
                                "Pointer changed concurrently; automatic restoration refused"
                            )
                        if pointer_before is None:
                            pointer_path.unlink()
                        else:
                            _write_atomic(pointer_path, pointer_before)
                    receipt["pointer_recovery"] = {"status": "restored_previous_pointer"}
                except (OSError, ValueError) as pointer_error:
                    receipt["pointer_recovery"] = {"status": "failed", "error": str(pointer_error)}
            if started:
                try:
                    _stop(config)
                except (OSError, ValueError, subprocess.SubprocessError) as cleanup:
                    receipt["cleanup_error"] = str(cleanup)
            if stopped and was_running:
                try:
                    if _protected(config) != {p: plan["files"][p] for p in _protected(config)}:
                        raise ValueError(
                            "Protected bindings drifted; automatic recovery is blocked"
                        )
                    _stop(config)
                    _start(config, old, old_identity)
                    receipt["recovery"] = {"status": "restored_previous_runtime", "runtime": old}
                except (OSError, ValueError, subprocess.SubprocessError) as recovery:
                    receipt["recovery"] = {"status": "failed", "error": str(recovery)}
            raise
        finally:
            receipt["finished_at"] = _now()
            try:
                receipt["observed_pointer_after"] = _read(pointer_path)
            except (OSError, ValueError) as pointer_error:
                receipt["observed_pointer_after"] = {"error": str(pointer_error)}
            _write_atomic(state / "last-operation.json", receipt)
            _write_atomic(state / "receipts" / f"{uuid.uuid4().hex}.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("action", choices=sorted(ACTIONS | {"status"}))
    parser.add_argument("--target")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            result = status(load_config(args.config))
        elif args.execute:
            if not args.expected_plan_sha256:
                raise ValueError("Execution requires the reviewed plan SHA-256")
            result = execute(args.config, args.action, args.target, args.expected_plan_sha256)
        else:
            result = preview(args.config, args.action, args.target)
        code = 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        result, code = {"status": "failed", "error": str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
