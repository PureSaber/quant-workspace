"""Local, reviewable release preparation and activation lifecycle.

The module deliberately separates source/environment preparation, acceptance execution,
and the small CAS-protected current-release pointer.  It never restarts a service or
mutates application data.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from quant_workspace.capabilities import _git
from quant_workspace.runtime_readiness import (
    _load as _load_runtime_profile,
)
from quant_workspace.runtime_readiness import (
    _source as _runtime_source,
)
from quant_workspace.runtime_readiness import (
    _validate as _validate_runtime_profile,
)
from quant_workspace.runtime_readiness import (
    bootstrap,
    check_runtime,
    python_path,
)

CANDIDATE_SCHEMA = "quant.release-candidate/v1"
SUITE_SCHEMA = "quant.release-acceptance-suite/v1"
EVIDENCE_SCHEMA = "quant.release-acceptance/v1"
STATE_SCHEMA = "quant.application-state-contract/v1"
RECEIPT_SCHEMA = "quant.release-receipt/v1"
CURRENT_SCHEMA = "quant.current-release/v1"
_CASE_KINDS = {"compatibility", "research", "state_compatibility"}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _object_hash(value: dict, field: str) -> str:
    payload = dict(value)
    payload.pop(field, None)
    return _sha(_canonical(payload))


def _relative(value: str, *, label: str = "path") -> Path:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError(f"{label} must be a canonical relative path")
    path = Path(value)
    if path.is_absolute() or path == Path(".") or ".." in path.parts or path.as_posix() != value:
        raise ValueError(f"{label} must be a canonical relative path")
    return path


def _inside(root: Path, value: str, *, label: str = "path") -> Path:
    root = root.resolve()
    path = root / _relative(value, label=label)
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"{label} escapes its root")
    return path


def _load_json(path: Path, *, maximum: int = 4_000_000) -> tuple[dict, bytes]:
    raw = Path(path).read_bytes()
    if len(raw) > maximum:
        raise ValueError(f"JSON file is too large: {path}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError(f"JSON object required: {path}")
    return value, raw


def _write_new(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        )
        stream.write(b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(
                json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
            )
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_state_contract(value: dict) -> None:
    if set(value) != {"schema_version", "name", "version"}:
        raise ValueError("State contract has invalid fields")
    if value["schema_version"] != STATE_SCHEMA:
        raise ValueError("Unsupported state contract")
    if not all(isinstance(value[key], str) and value[key] for key in ("name", "version")):
        raise ValueError("State contract name and version are required")


def _validate_suite(value: dict, revisions: dict[str, str]) -> None:
    if set(value) != {"schema_version", "source_revisions", "cases"}:
        raise ValueError("Acceptance suite has invalid fields")
    if value["schema_version"] != SUITE_SCHEMA or value["source_revisions"] != revisions:
        raise ValueError("Acceptance suite does not bind the candidate source revisions")
    cases = value["cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("Acceptance suite must contain cases")
    identities: set[str] = set()
    for case in cases:
        base = {"id", "kind", "project", "cwd", "args", "outputs"}
        state = {"from_state_sha256", "to_state_sha256"}
        expected = base | state if case.get("kind") == "state_compatibility" else base
        if not isinstance(case, dict) or set(case) != expected:
            raise ValueError("Acceptance case has invalid fields")
        if (
            not isinstance(case["id"], str)
            or not case["id"]
            or case["id"] in identities
            or case["kind"] not in _CASE_KINDS
            or case["project"] not in revisions
        ):
            raise ValueError("Acceptance case identity is invalid")
        identities.add(case["id"])
        _relative(case["cwd"], label="acceptance cwd")
        if (
            not isinstance(case["args"], list)
            or not case["args"]
            or not all(isinstance(arg, str) and arg and "\x00" not in arg for arg in case["args"])
        ):
            raise ValueError("Acceptance case args must be a non-empty argument array")
        if not isinstance(case["outputs"], list) or not case["outputs"]:
            raise ValueError("Acceptance case must bind expected output files")
        output_paths: set[str] = set()
        for output in case["outputs"]:
            if not isinstance(output, dict) or set(output) != {"path", "sha256"}:
                raise ValueError("Acceptance output has invalid fields")
            _relative(output["path"], label="acceptance output")
            if output["path"] in output_paths or not isinstance(output["sha256"], str):
                raise ValueError("Acceptance outputs must be unique and hashed")
            output_paths.add(output["path"])
            if len(output["sha256"]) != 64 or any(
                c not in "0123456789abcdef" for c in output["sha256"]
            ):
                raise ValueError("Acceptance output requires a SHA-256 digest")
        if case["kind"] == "state_compatibility":
            for key in state:
                digest = case[key]
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)
                ):
                    raise ValueError("State compatibility case requires exact contract digests")


def _runtime_snapshot(profile_path: Path, root: Path) -> tuple[dict, str, list[dict]]:
    profile, profile_sha = _load_runtime_profile(profile_path)
    rows = []
    for item in profile["projects"]:
        repo, issues = _runtime_source(item, root.resolve())
        if issues:
            raise ValueError(f"Source does not match runtime profile for {item['id']}: {issues}")
        rows.append(
            {
                "id": item["id"],
                "repo": item["repo"],
                "revision": _git(repo, "rev-parse", "HEAD"),
                "lock": item["lock"],
                "lock_sha256": _sha((repo / item["lock"]).read_bytes()),
                "distribution": item["distribution"],
                "python": item["python"],
                "environment": item["environment"],
            }
        )
    return profile, profile_sha, rows


def _validate_candidate(value: dict) -> None:
    fields = {
        "schema_version",
        "created_at",
        "candidate_sha256",
        "runtime_profile_sha256",
        "runtime_profile",
        "projects",
        "readiness",
        "acceptance_suite_sha256",
        "acceptance_suite",
        "state_contract_sha256",
        "state_contract",
        "actual_deployment",
        "claims",
    }
    if set(value) != fields or value.get("schema_version") != CANDIDATE_SCHEMA:
        raise ValueError("Invalid release candidate fields")
    if value.get("candidate_sha256") != _object_hash(value, "candidate_sha256"):
        raise ValueError("Release candidate digest mismatch")
    profile = value["runtime_profile"]
    _validate_runtime_profile(profile)
    if _sha(_canonical(profile)) != value["runtime_profile_sha256"]:
        raise ValueError("Embedded runtime profile digest mismatch")
    expected_projects = [
        {
            "id": item["id"],
            "repo": item["repo"],
            "revision": item["revision"],
            "lock": item["lock"],
            "lock_sha256": item["lock_sha256"],
            "distribution": item["distribution"],
            "python": item["python"],
            "environment": item["environment"],
        }
        for item in profile["projects"]
    ]
    if value["projects"] != expected_projects:
        raise ValueError("Candidate project pins do not match its runtime profile")
    revisions = {row["id"]: row["revision"] for row in value["projects"]}
    _validate_suite(value["acceptance_suite"], revisions)
    if _sha(_canonical(value["acceptance_suite"])) != value["acceptance_suite_sha256"]:
        raise ValueError("Embedded acceptance suite digest mismatch")
    _validate_state_contract(value["state_contract"])
    if _sha(_canonical(value["state_contract"])) != value["state_contract_sha256"]:
        raise ValueError("Embedded state contract digest mismatch")
    if value["readiness"].get("status") != "ready":
        raise ValueError("Candidate readiness is not ready")
    if value["claims"] != {
        "acceptance_executed": False,
        "service_restarted": False,
        "business_data_migrated": False,
    }:
        raise ValueError("Invalid candidate claims")


def load_candidate(path: Path) -> tuple[dict, str]:
    value, raw = _load_json(path)
    _validate_candidate(value)
    return value, _sha(raw)


def current_release(state_dir: Path) -> dict | None:
    path = Path(state_dir) / "current.json"
    if not path.is_file():
        return None
    value, _ = _load_json(path)
    if set(value) != {"schema_version", "candidate_sha256", "receipt", "receipt_sha256"}:
        raise ValueError("Current release pointer has invalid fields")
    if value["schema_version"] != CURRENT_SCHEMA:
        raise ValueError("Unsupported current release pointer")
    if not isinstance(value["candidate_sha256"], str) or len(value["candidate_sha256"]) != 64:
        raise ValueError("Current release candidate digest is invalid")
    receipt_path = _inside(Path(state_dir), value["receipt"], label="receipt path")
    raw = receipt_path.read_bytes()
    if _sha(raw) != value["receipt_sha256"]:
        raise ValueError("Current release receipt digest mismatch")
    receipt = json.loads(raw)
    receipt_fields = {
        "schema_version",
        "created_at",
        "action",
        "candidate_sha256",
        "previous_candidate_sha256",
        "acceptance_sha256",
        "state_contract_sha256",
        "prepared_root",
        "business_data_changed",
        "service_restarted",
        "activation_instruction",
    }
    if (
        not isinstance(receipt, dict)
        or set(receipt) != receipt_fields
        or receipt.get("schema_version") != RECEIPT_SCHEMA
        or receipt.get("candidate_sha256") != value["candidate_sha256"]
        or receipt.get("action") not in {"activate", "rollback"}
        or receipt.get("business_data_changed") is not False
        or receipt.get("service_restarted") is not False
    ):
        raise ValueError("Current release receipt does not match its pointer")
    return {**value, "receipt_record": receipt}


def create_candidate(
    runtime_profile: Path,
    source_root: Path,
    acceptance_suite: Path,
    state_contract: Path,
    out: Path,
    *,
    state_dir: Path | None = None,
    created_at: str | None = None,
) -> dict:
    """Create a candidate only from a clean, ready, exactly pinned local stack."""
    source_root = Path(source_root).resolve()
    profile, profile_sha, projects = _runtime_snapshot(Path(runtime_profile), source_root)
    readiness = check_runtime(Path(runtime_profile), source_root)
    if readiness["profile_sha256"] != profile_sha or readiness["status"] != "ready":
        raise ValueError(f"Runtime profile is not ready: {readiness}")
    suite, _ = _load_json(Path(acceptance_suite))
    revisions = {row["id"]: row["revision"] for row in projects}
    _validate_suite(suite, revisions)
    contract, _ = _load_json(Path(state_contract))
    _validate_state_contract(contract)
    actual = {"status": "unmanaged", "candidate_sha256": None, "receipt_sha256": None}
    if state_dir is not None and (current := current_release(Path(state_dir))) is not None:
        actual = {
            "status": "recorded",
            "candidate_sha256": current["candidate_sha256"],
            "receipt_sha256": current["receipt_sha256"],
        }
    candidate = {
        "schema_version": CANDIDATE_SCHEMA,
        "created_at": created_at or _now(),
        "candidate_sha256": "",
        "runtime_profile_sha256": _sha(_canonical(profile)),
        "runtime_profile": profile,
        "projects": projects,
        "readiness": readiness,
        "acceptance_suite_sha256": _sha(_canonical(suite)),
        "acceptance_suite": suite,
        "state_contract_sha256": _sha(_canonical(contract)),
        "state_contract": contract,
        "actual_deployment": actual,
        "claims": {
            "acceptance_executed": False,
            "service_restarted": False,
            "business_data_migrated": False,
        },
    }
    candidate["candidate_sha256"] = _object_hash(candidate, "candidate_sha256")
    _validate_candidate(candidate)
    # Detect a source/profile change after all environment and evidence reads.
    _, after_sha, after_projects = _runtime_snapshot(Path(runtime_profile), source_root)
    if after_sha != profile_sha or after_projects != projects:
        raise ValueError("Source or runtime profile changed while creating candidate")
    _write_new(Path(out), candidate)
    return candidate


def _candidate_sources_match(candidate: dict, root: Path) -> None:
    for item in candidate["projects"]:
        repo = _inside(root, item["repo"], label="project repo")
        if Path(_git(repo, "rev-parse", "--show-toplevel")).resolve() != repo.resolve():
            raise ValueError(f"Project is not an independent checkout: {item['id']}")
        if _git(repo, "rev-parse", "HEAD") != item["revision"]:
            raise ValueError(f"Project revision mismatch: {item['id']}")
        if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ValueError(f"Project checkout is dirty: {item['id']}")
        lock = _inside(repo, item["lock"], label="project lock")
        if _sha(lock.read_bytes()) != item["lock_sha256"]:
            raise ValueError(f"Project lock mismatch: {item['id']}")


def prepare_candidate(
    candidate_path: Path,
    source_root: Path,
    destination: Path,
    *,
    execute: bool = False,
    build_environments: bool = False,
) -> dict:
    """Plan or materialize clean local clones; environment creation stays explicit."""
    candidate, candidate_file_sha = load_candidate(Path(candidate_path))
    source_root = Path(source_root).resolve()
    destination = Path(destination).resolve()
    _candidate_sources_match(candidate, source_root)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Candidate destination must not exist")
    commands = []
    for item in candidate["projects"]:
        source = _inside(source_root, item["repo"], label="source repo")
        target = destination / item["repo"]
        commands.extend(
            [
                [
                    "git",
                    "-c",
                    "core.autocrlf=false",
                    "clone",
                    "--local",
                    "--no-hardlinks",
                    "--no-checkout",
                    str(source),
                    str(target),
                ],
                ["git", "-C", str(target), "checkout", "--detach", item["revision"]],
            ]
        )
    if build_environments and not execute:
        raise ValueError("Environment build requires explicit execution")
    if not execute:
        return {
            "schema_version": "quant.release-preparation/v1",
            "candidate_sha256": candidate["candidate_sha256"],
            "executed": False,
            "build_environments": build_environments,
            "commands": commands,
            "source_clone_network_required": False,
            "environment_install_may_use_configured_package_source": build_environments,
        }

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.prepare-", dir=destination.parent))
    try:
        for item in candidate["projects"]:
            source = _inside(source_root, item["repo"], label="source repo")
            target = staging / item["repo"]
            target.parent.mkdir(parents=True, exist_ok=True)
            clone = [
                "git",
                "-c",
                "core.autocrlf=false",
                "clone",
                "--local",
                "--no-hardlinks",
                "--no-checkout",
                str(source),
                str(target),
            ]
            subprocess.run(clone, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-C", str(target), "checkout", "--detach", item["revision"]],
                check=True,
                capture_output=True,
                text=True,
            )
        metadata = staging / ".quant-delivery"
        metadata.mkdir()
        (metadata / "runtime-profile.json").write_bytes(_canonical(candidate["runtime_profile"]))
        (metadata / "acceptance-suite.json").write_bytes(_canonical(candidate["acceptance_suite"]))
        (metadata / "state-contract.json").write_bytes(_canonical(candidate["state_contract"]))
        (metadata / "candidate.json").write_bytes(
            json.dumps(candidate, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
            + b"\n"
        )
        (metadata / "candidate-file.sha256").write_text(candidate_file_sha + "\n", encoding="ascii")
        _candidate_sources_match(candidate, staging)
        _candidate_sources_match(candidate, source_root)
        _candidate_sources_match(candidate, staging)
        staging.replace(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if build_environments:
        # Venv entry points and editable installs contain absolute paths. Build
        # after publishing the source directory, and never relocate the runtime.
        # Retain an incomplete new destination and bootstrap logs on failure;
        # acceptance still requires a complete, ready environment.
        profile_path = destination / ".quant-delivery/runtime-profile.json"
        for item in candidate["projects"]:
            bootstrap(profile_path, destination, item["id"], execute=True)
        if check_runtime(profile_path, destination)["status"] != "ready":
            raise ValueError("Prepared environments did not pass runtime verification")
        _candidate_sources_match(candidate, source_root)
        _candidate_sources_match(candidate, destination)
    return {
        "schema_version": "quant.release-preparation/v1",
        "candidate_sha256": candidate["candidate_sha256"],
        "executed": True,
        "build_environments": build_environments,
        "destination": str(destination),
        "commands": commands,
        "source_clone_network_required": False,
        "environment_install_may_use_configured_package_source": build_environments,
    }


def _verify_prepared(candidate: dict, prepared_root: Path) -> dict:
    metadata = prepared_root / ".quant-delivery"
    embedded, _ = _load_json(metadata / "candidate.json")
    _validate_candidate(embedded)
    if embedded["candidate_sha256"] != candidate["candidate_sha256"]:
        raise ValueError("Prepared candidate identity mismatch")
    profile_path = metadata / "runtime-profile.json"
    if _sha(profile_path.read_bytes()) != candidate["runtime_profile_sha256"]:
        raise ValueError("Prepared runtime profile mismatch")
    _candidate_sources_match(candidate, prepared_root)
    readiness = check_runtime(profile_path, prepared_root)
    if readiness["status"] != "ready":
        raise ValueError(f"Prepared runtime is not ready: {readiness}")
    return readiness


def run_acceptance(
    candidate_path: Path,
    prepared_root: Path,
    out: Path,
    *,
    timeout: int = 900,
) -> dict:
    """Execute the candidate-bound commands and hash their real output artifacts."""
    candidate, _ = load_candidate(Path(candidate_path))
    prepared_root = Path(prepared_root).resolve()
    before = _verify_prepared(candidate, prepared_root)
    results = []
    for case in candidate["acceptance_suite"]["cases"]:
        item = next(row for row in candidate["projects"] if row["id"] == case["project"])
        executable = python_path(_inside(prepared_root, item["repo"]) / item["environment"])
        command = [str(executable), *case["args"]]
        cwd = _inside(prepared_root, case["cwd"], label="acceptance cwd")
        if not cwd.is_dir():
            raise ValueError(f"Acceptance cwd is missing: {case['id']}")
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            returncode = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            returncode = None
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
        outputs = []
        output_ok = True
        for expected in case["outputs"]:
            path = _inside(prepared_root, expected["path"], label="acceptance output")
            if path.is_file() and not path.is_symlink():
                actual = _sha(path.read_bytes())
            else:
                actual = None
            matches = actual == expected["sha256"]
            output_ok = output_ok and matches
            outputs.append({"path": expected["path"], "sha256": actual, "matches": matches})
        results.append(
            {
                "id": case["id"],
                "kind": case["kind"],
                "command": command,
                "returncode": returncode,
                "stdout_sha256": _sha(str(stdout).encode("utf-8")),
                "stderr_sha256": _sha(str(stderr).encode("utf-8")),
                "outputs": outputs,
                "status": "passed" if returncode == 0 and output_ok else "failed",
            }
        )
    after = _verify_prepared(candidate, prepared_root)
    evidence = {
        "schema_version": EVIDENCE_SCHEMA,
        "created_at": _now(),
        "acceptance_sha256": "",
        "candidate_sha256": candidate["candidate_sha256"],
        "acceptance_suite_sha256": candidate["acceptance_suite_sha256"],
        "runtime_before_sha256": _sha(_canonical(before)),
        "runtime_after_sha256": _sha(_canonical(after)),
        "status": "accepted" if all(row["status"] == "passed" for row in results) else "blocked",
        "cases": results,
        "claims": {
            "commands_executed": True,
            "output_bytes_verified": True,
            "service_restarted": False,
            "business_data_migrated": False,
            "market_data_certified": False,
        },
    }
    evidence["acceptance_sha256"] = _object_hash(evidence, "acceptance_sha256")
    _write_new(Path(out), evidence)
    return evidence


def verify_acceptance(candidate: dict, evidence_path: Path, prepared_root: Path) -> dict:
    evidence, _ = _load_json(Path(evidence_path))
    if evidence.get("schema_version") != EVIDENCE_SCHEMA:
        raise ValueError("Unsupported acceptance evidence")
    if evidence.get("acceptance_sha256") != _object_hash(evidence, "acceptance_sha256"):
        raise ValueError("Acceptance evidence digest mismatch")
    if (
        evidence.get("candidate_sha256") != candidate["candidate_sha256"]
        or evidence.get("acceptance_suite_sha256") != candidate["acceptance_suite_sha256"]
        or evidence.get("status") != "accepted"
    ):
        raise ValueError("Acceptance evidence does not accept this candidate")
    expected_cases = {case["id"]: case for case in candidate["acceptance_suite"]["cases"]}
    actual_cases = evidence.get("cases")
    if not isinstance(actual_cases, list) or {row.get("id") for row in actual_cases} != set(
        expected_cases
    ):
        raise ValueError("Acceptance evidence case set mismatch")
    for result in actual_cases:
        case = expected_cases[result["id"]]
        if result.get("status") != "passed" or result.get("returncode") != 0:
            raise ValueError(f"Acceptance case did not pass: {result['id']}")
        recorded = {row.get("path"): row for row in result.get("outputs", [])}
        if set(recorded) != {row["path"] for row in case["outputs"]}:
            raise ValueError("Acceptance output set mismatch")
        for expected in case["outputs"]:
            path = _inside(Path(prepared_root), expected["path"], label="acceptance output")
            actual = _sha(path.read_bytes()) if path.is_file() and not path.is_symlink() else None
            row = recorded[expected["path"]]
            if (
                actual != expected["sha256"]
                or row.get("sha256") != actual
                or row.get("matches") is not True
            ):
                raise ValueError(f"Acceptance output mismatch: {expected['path']}")
    readiness = _verify_prepared(candidate, Path(prepared_root).resolve())
    if _sha(_canonical(readiness)) != evidence["runtime_after_sha256"]:
        raise ValueError("Runtime state changed since acceptance")
    return evidence


@contextmanager
def _state_lock(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = state_dir / ".release.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("Release state is locked by another operation") from exc
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def activate_candidate(
    candidate_path: Path,
    evidence_path: Path,
    prepared_root: Path,
    state_dir: Path,
    *,
    expected_current: str | None,
    action: str = "activate",
) -> dict:
    """CAS-switch release metadata only; application state and services are untouched."""
    if action not in {"activate", "rollback"}:
        raise ValueError("Release action must be activate or rollback")
    candidate, _ = load_candidate(Path(candidate_path))
    evidence = verify_acceptance(candidate, Path(evidence_path), Path(prepared_root))
    state_dir = Path(state_dir).resolve()
    with _state_lock(state_dir):
        current = current_release(state_dir)
        actual_current = current["candidate_sha256"] if current else None
        if actual_current != expected_current:
            raise ValueError(
                f"Current release CAS mismatch: expected {expected_current!r}, found {actual_current!r}"
            )
        if current:
            old_state = current["receipt_record"]["state_contract_sha256"]
            new_state = candidate["state_contract_sha256"]
            if old_state != new_state:
                transitions = {
                    (case["from_state_sha256"], case["to_state_sha256"])
                    for case in candidate["acceptance_suite"]["cases"]
                    if case["kind"] == "state_compatibility"
                    and next(row for row in evidence["cases"] if row["id"] == case["id"])["status"]
                    == "passed"
                }
                if (old_state, new_state) not in transitions:
                    raise ValueError(
                        "Application state compatibility was not executed for this transition"
                    )
        candidate_store = state_dir / "candidates" / f"{candidate['candidate_sha256']}.json"
        if candidate_store.exists():
            stored, _ = load_candidate(candidate_store)
            if stored != candidate:
                raise ValueError("Candidate registry collision")
        else:
            _write_new(candidate_store, candidate)
        receipt = {
            "schema_version": RECEIPT_SCHEMA,
            "created_at": _now(),
            "action": action,
            "candidate_sha256": candidate["candidate_sha256"],
            "previous_candidate_sha256": actual_current,
            "acceptance_sha256": evidence["acceptance_sha256"],
            "state_contract_sha256": candidate["state_contract_sha256"],
            "prepared_root": str(Path(prepared_root).resolve()),
            "business_data_changed": False,
            "service_restarted": False,
            "activation_instruction": "Apply the recorded target configuration and restart the service manually.",
        }
        receipt_name = f"receipts/{candidate['candidate_sha256']}-{uuid.uuid4().hex}.json"
        receipt_path = state_dir / receipt_name
        _write_new(receipt_path, receipt)
        receipt_sha = _sha(receipt_path.read_bytes())
        pointer = {
            "schema_version": CURRENT_SCHEMA,
            "candidate_sha256": candidate["candidate_sha256"],
            "receipt": receipt_name,
            "receipt_sha256": receipt_sha,
        }
        _write_atomic(state_dir / "current.json", pointer)
    return {**pointer, "receipt_record": receipt, "manual_service_activation_required": True}
