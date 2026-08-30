"""Strict, content-addressed M7 performance and market-data certification."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from zipfile import BadZipFile, ZipFile

from quant_workspace.stack_manifest import ValidationIssue

M7_CERTIFICATION_SCHEMA_VERSION = "1.0.0"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_CRYPTO_CAPABILITIES = frozenset(
    {"btc-spot-l2", "eth-spot-l2", "btc-usdt-perpetual-l2", "eth-usdt-perpetual-l2"}
)
_REQUIRED_DOMESTIC_CAPABILITIES = frozenset({"domestic-l2-replay"})
_REQUIRED_CI_PROJECTS = frozenset({"quant-data-kit", "quant-execution", "quant-workspace"})
_REQUIRED_CI_PROJECT_ORDER = ("quant-data-kit", "quant-execution", "quant-workspace")
_REQUIRED_PYTHONS = ("3.10", "3.11", "3.12")
_REQUIRED_M7_WORKFLOW_PATH = ".github/workflows/m7-certification.yml"
_REQUIRED_EVIDENCE_BY_PROJECT = {
    "quant-data-kit": ("data_standardization", "crypto_l2", "domestic_l2"),
    "quant-execution": ("execution_replay",),
    "quant-workspace": (),
}
_BENCHMARK_EVIDENCE_SCHEMA = "puresaber.m7-benchmark-evidence@1.0.0"
_MARKET_EVIDENCE_SCHEMA = "puresaber.m7-market-data-evidence@1.0.0"
_MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
_GITHUB_RUN_URL = re.compile(
    r"^https://github\.com/PureSaber/(?P<project>[a-z0-9-]+)/actions/runs/(?P<run_id>[1-9][0-9]*)$"
)
_REQUIRED_CRYPTO_STREAMS = (
    "binance:spot:BTCUSDT",
    "binance:spot:ETHUSDT",
    "binance:usdt-perpetual:BTCUSDT",
    "binance:usdt-perpetual:ETHUSDT",
    "okx:spot:BTC-USDT",
    "okx:spot:ETH-USDT",
    "okx:usdt-perpetual:BTC-USDT-SWAP",
    "okx:usdt-perpetual:ETH-USDT-SWAP",
)
_DATA_ASSERTIONS = {
    "accepted_all",
    "quarantine_zero",
    "strict_reload",
    "deterministic_artifacts",
    "pit_passed",
    "l2_checkpoints_match",
    "artifacts_retained",
}
_EXECUTION_ASSERTIONS = {
    "all_events_processed",
    "order_fill_conservation",
    "ledger_balanced",
    "nav_reconciled",
    "strict_reload",
    "deterministic_artifacts",
    "artifacts_retained",
}


def _exact_fields(payload: dict[str, Any], expected: set[str], where: str) -> None:
    missing = sorted(expected - set(payload))
    unknown = sorted(set(payload) - expected)
    if missing or unknown:
        raise ValueError(f"{where} fields changed: missing={missing}, unknown={unknown}")


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{field} must be an object")
    return value


def _array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be an array")
    return value


def _string_array(value: Any, field: str) -> tuple[str, ...]:
    return tuple(_string(item, f"{field} item") for item in _array(value, field))


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a boolean")
    return value


@dataclass(frozen=True)
class EvidenceFile:
    path: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EvidenceFile:
        _exact_fields(payload, {"path", "sha256"}, "evidence")
        return cls(
            path=_string(payload["path"], "evidence path"),
            sha256=_string(payload["sha256"], "evidence sha256"),
        )


@dataclass(frozen=True)
class BenchmarkRun:
    run: int
    events: int
    events_per_second: float
    peak_rss_gib: float
    artifact_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run,
            "events": self.events,
            "events_per_second": float(self.events_per_second),
            "peak_rss_gib": float(self.peak_rss_gib),
            "artifact_sha256": self.artifact_sha256,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BenchmarkRun:
        _exact_fields(
            payload,
            {"run", "events", "events_per_second", "peak_rss_gib", "artifact_sha256"},
            "benchmark run",
        )
        return cls(
            run=_integer(payload["run"], "run"),
            events=_integer(payload["events"], "events"),
            events_per_second=_number(payload["events_per_second"], "events_per_second"),
            peak_rss_gib=_number(payload["peak_rss_gib"], "peak_rss_gib"),
            artifact_sha256=_string(payload["artifact_sha256"], "artifact_sha256"),
        )


@dataclass(frozen=True)
class BenchmarkEvidence:
    evidence: EvidenceFile
    measurement_scope: str
    runs: tuple[BenchmarkRun, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence": self.evidence.to_dict(),
            "measurement_scope": self.measurement_scope,
            "runs": [run.to_dict() for run in self.runs],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BenchmarkEvidence:
        _exact_fields(payload, {"evidence", "measurement_scope", "runs"}, "benchmark")
        return cls(
            evidence=EvidenceFile.from_dict(_object(payload["evidence"], "benchmark evidence")),
            measurement_scope=_string(payload["measurement_scope"], "measurement_scope"),
            runs=tuple(
                BenchmarkRun.from_dict(_object(item, "benchmark run"))
                for item in _array(payload["runs"], "benchmark runs")
            ),
        )


@dataclass(frozen=True)
class MarketDataEvidence:
    status: Literal["market-data-certified", "fixture-certified"]
    providers: tuple[str, ...]
    capabilities: tuple[str, ...]
    window_start: str
    window_end: str
    continuous_days: int
    evidence: EvidenceFile

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "providers": list(self.providers),
            "capabilities": list(self.capabilities),
            "window_start": self.window_start,
            "window_end": self.window_end,
            "continuous_days": self.continuous_days,
            "evidence": self.evidence.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> MarketDataEvidence:
        _exact_fields(
            payload,
            {
                "status",
                "providers",
                "capabilities",
                "window_start",
                "window_end",
                "continuous_days",
                "evidence",
            },
            "market data",
        )
        return cls(
            status=_string(payload["status"], "market status"),  # type: ignore[arg-type]
            providers=_string_array(payload["providers"], "providers"),
            capabilities=_string_array(payload["capabilities"], "capabilities"),
            window_start=_string(payload["window_start"], "window_start"),
            window_end=_string(payload["window_end"], "window_end"),
            continuous_days=_integer(payload["continuous_days"], "continuous_days"),
            evidence=EvidenceFile.from_dict(_object(payload["evidence"], "market evidence")),
        )


@dataclass(frozen=True)
class EvidenceArtifact:
    label: str
    artifact_id: int
    archive_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "artifact_id": self.artifact_id,
            "archive_sha256": self.archive_sha256,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EvidenceArtifact:
        _exact_fields(payload, {"label", "artifact_id", "archive_sha256"}, "evidence artifact")
        return cls(
            label=_string(payload["label"], "evidence artifact label"),
            artifact_id=_integer(payload["artifact_id"], "evidence artifact id"),
            archive_sha256=_string(payload["archive_sha256"], "evidence artifact archive_sha256"),
        )


@dataclass(frozen=True)
class CIResult:
    project: str
    commit: str
    python_versions: tuple[str, ...]
    status: Literal["success", "failure"]
    run_url: str
    workflow_path: str
    run_attempt: int
    evidence_artifacts: tuple[EvidenceArtifact, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "commit": self.commit,
            "python_versions": list(self.python_versions),
            "status": self.status,
            "run_url": self.run_url,
            "workflow_path": self.workflow_path,
            "run_attempt": self.run_attempt,
            "evidence_artifacts": [item.to_dict() for item in self.evidence_artifacts],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CIResult:
        _exact_fields(
            payload,
            {
                "project",
                "commit",
                "python_versions",
                "status",
                "run_url",
                "workflow_path",
                "run_attempt",
                "evidence_artifacts",
            },
            "CI result",
        )
        return cls(
            project=_string(payload["project"], "CI project"),
            commit=_string(payload["commit"], "CI commit"),
            python_versions=_string_array(payload["python_versions"], "CI python_versions"),
            status=_string(payload["status"], "CI status"),  # type: ignore[arg-type]
            run_url=_string(payload["run_url"], "CI run_url"),
            workflow_path=_string(payload["workflow_path"], "CI workflow_path"),
            run_attempt=_integer(payload["run_attempt"], "CI run_attempt"),
            evidence_artifacts=tuple(
                EvidenceArtifact.from_dict(_object(item, "evidence artifact"))
                for item in _array(payload["evidence_artifacts"], "CI evidence_artifacts")
            ),
        )


@dataclass(frozen=True)
class M7Certification:
    schema_version: str
    created_at: str
    data_standardization: BenchmarkEvidence
    execution_replay: BenchmarkEvidence
    crypto_l2: MarketDataEvidence
    domestic_l2: MarketDataEvidence
    ci: tuple[CIResult, ...]
    release_status: Literal["rc-ready", "ga-ready"]
    certification_sha256: str

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "data_standardization": self.data_standardization.to_dict(),
            "execution_replay": self.execution_replay.to_dict(),
            "crypto_l2": self.crypto_l2.to_dict(),
            "domestic_l2": self.domestic_l2.to_dict(),
            "ci": [item.to_dict() for item in self.ci],
            "release_status": self.release_status,
        }
        if include_hash:
            payload["certification_sha256"] = self.certification_sha256
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> M7Certification:
        try:
            _exact_fields(
                payload,
                {
                    "schema_version",
                    "created_at",
                    "data_standardization",
                    "execution_replay",
                    "crypto_l2",
                    "domestic_l2",
                    "ci",
                    "release_status",
                    "certification_sha256",
                },
                "M7 certification",
            )
            return cls(
                schema_version=_string(payload["schema_version"], "schema_version"),
                created_at=_string(payload["created_at"], "created_at"),
                data_standardization=BenchmarkEvidence.from_dict(
                    _object(payload["data_standardization"], "data_standardization")
                ),
                execution_replay=BenchmarkEvidence.from_dict(
                    _object(payload["execution_replay"], "execution_replay")
                ),
                crypto_l2=MarketDataEvidence.from_dict(_object(payload["crypto_l2"], "crypto_l2")),
                domestic_l2=MarketDataEvidence.from_dict(
                    _object(payload["domestic_l2"], "domestic_l2")
                ),
                ci=tuple(
                    CIResult.from_dict(_object(item, "CI result"))
                    for item in _array(payload["ci"], "CI results")
                ),
                release_status=_string(payload["release_status"], "release_status"),  # type: ignore[arg-type]
                certification_sha256=_string(
                    payload["certification_sha256"], "certification_sha256"
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid M7Certification payload: {exc}") from exc


@dataclass(frozen=True)
class M7ValidationResult:
    valid: bool
    rc_ready: bool
    ga_ready: bool
    issues: tuple[ValidationIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "rc_ready": self.rc_ready,
            "ga_ready": self.ga_ready,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def canonical_certification_bytes(
    certification: M7Certification,
    *,
    include_hash: bool = True,
) -> bytes:
    return json.dumps(
        certification.to_dict(include_hash=include_hash),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def certification_hash(certification: M7Certification) -> str:
    return hashlib.sha256(
        canonical_certification_bytes(certification, include_hash=False)
    ).hexdigest()


def seal_certification(certification: M7Certification) -> M7Certification:
    return replace(certification, certification_sha256=certification_hash(certification))


def _timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _evidence_payload(
    evidence: EvidenceFile,
    label: str,
    evidence_root: Path | None,
) -> tuple[dict[str, Any] | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    relative = PurePosixPath(evidence.path)
    if not evidence.path or relative.is_absolute() or ".." in relative.parts:
        issues.append(ValidationIssue("error", "EVIDENCE_PATH_UNSAFE", label))
        return None, issues
    if not _SHA256.fullmatch(evidence.sha256):
        issues.append(ValidationIssue("error", "EVIDENCE_HASH_INVALID", label))
    if evidence_root is None:
        issues.append(ValidationIssue("error", "EVIDENCE_ROOT_REQUIRED", label))
        return None, issues
    root = evidence_root.resolve()
    candidate = (root / Path(*relative.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        issues.append(ValidationIssue("error", "EVIDENCE_PATH_ESCAPE", label))
        return None, issues
    if not candidate.is_file():
        issues.append(ValidationIssue("error", "EVIDENCE_MISSING", label))
        return None, issues
    try:
        body = candidate.read_bytes()
    except OSError:
        issues.append(ValidationIssue("error", "EVIDENCE_UNREADABLE", label))
        return None, issues
    if _SHA256.fullmatch(evidence.sha256) and hashlib.sha256(body).hexdigest() != evidence.sha256:
        issues.append(ValidationIssue("error", "EVIDENCE_CONTENT_CHANGED", label))
        return None, issues
    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError):
        issues.append(ValidationIssue("error", "EVIDENCE_JSON_INVALID", label))
        return None, issues
    if not isinstance(payload, dict):
        issues.append(ValidationIssue("error", "EVIDENCE_JSON_INVALID", label))
        return None, issues
    try:
        canonical = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError):
        issues.append(ValidationIssue("error", "EVIDENCE_JSON_INVALID", label))
        return None, issues
    if body != canonical:
        issues.append(ValidationIssue("error", "EVIDENCE_NOT_CANONICAL", label))
        return None, issues
    return payload, issues


def _evidence_schema_issue(label: str, exc: Exception) -> ValidationIssue:
    return ValidationIssue("error", "EVIDENCE_SCHEMA_INVALID", f"{label}: {exc}")


def _benchmark_semantic_issues(
    evidence: BenchmarkEvidence,
    *,
    label: str,
    expected_project: str,
    expected_commit: str | None,
    evidence_root: Path | None,
) -> list[ValidationIssue]:
    payload, issues = _evidence_payload(evidence.evidence, label, evidence_root)
    if payload is None:
        return issues
    try:
        _exact_fields(
            payload,
            {
                "schema_version",
                "kind",
                "project",
                "source_commit",
                "working_tree_dirty",
                "measurement_scope",
                "runs",
                "assertions",
            },
            f"{label} evidence",
        )
        if (
            _string(payload["schema_version"], "evidence schema_version")
            != _BENCHMARK_EVIDENCE_SCHEMA
        ):
            raise ValueError("benchmark evidence schema_version is unsupported")
        if _string(payload["kind"], "evidence kind") != label:
            raise ValueError("benchmark evidence kind does not match certification section")
        if _string(payload["project"], "evidence project") != expected_project:
            raise ValueError("benchmark evidence project does not match certification section")
        source_commit = _string(payload["source_commit"], "evidence source_commit")
        if not _SHA40.fullmatch(source_commit):
            raise ValueError("benchmark evidence source_commit is invalid")
        if expected_commit is None or source_commit != expected_commit:
            raise ValueError("benchmark evidence source_commit does not match CI commit")
        if _boolean(payload["working_tree_dirty"], "working_tree_dirty"):
            raise ValueError("benchmark evidence was produced from a dirty tree")
        if (
            _string(payload["measurement_scope"], "evidence measurement_scope")
            != evidence.measurement_scope
        ):
            raise ValueError("benchmark evidence measurement_scope does not match certification")
        evidence_runs = _array(payload["runs"], "evidence runs")
        if len(evidence_runs) != len(evidence.runs):
            raise ValueError("benchmark evidence run count does not match certification")
        for claimed, raw in zip(evidence.runs, evidence_runs, strict=True):
            run = _object(raw, "evidence run")
            _exact_fields(
                run,
                {"run", "events", "events_per_second", "peak_rss_gib", "artifact_sha256"},
                "benchmark evidence run",
            )
            if (
                _integer(run["run"], "evidence run") != claimed.run
                or _integer(run["events"], "evidence events") != claimed.events
                or _number(run["events_per_second"], "evidence events_per_second")
                != claimed.events_per_second
                or _number(run["peak_rss_gib"], "evidence peak_rss_gib") != claimed.peak_rss_gib
                or _string(run["artifact_sha256"], "evidence artifact_sha256")
                != claimed.artifact_sha256
            ):
                raise ValueError("benchmark evidence run does not match certification claim")
        assertions = _object(payload["assertions"], "evidence assertions")
        expected_assertions = (
            _DATA_ASSERTIONS if label == "data_standardization" else _EXECUTION_ASSERTIONS
        )
        _exact_fields(assertions, expected_assertions, f"{label} assertions")
        if not all(_boolean(assertions[name], f"assertion {name}") for name in expected_assertions):
            raise ValueError("benchmark evidence contains a failed assertion")
    except (KeyError, TypeError, ValueError) as exc:
        issues.append(_evidence_schema_issue(label, exc))
    return issues


def _benchmark_issues(
    evidence: BenchmarkEvidence,
    *,
    label: str,
    minimum_rate: float,
    expected_project: str,
    expected_commit: str | None,
    evidence_root: Path | None,
) -> list[ValidationIssue]:
    issues = _benchmark_semantic_issues(
        evidence,
        label=label,
        expected_project=expected_project,
        expected_commit=expected_commit,
        evidence_root=evidence_root,
    )
    if not evidence.measurement_scope.strip():
        issues.append(ValidationIssue("error", "MEASUREMENT_SCOPE_MISSING", label))
    if tuple(run.run for run in evidence.runs) != (1, 2, 3):
        issues.append(ValidationIssue("error", "BENCHMARK_RUNS_INVALID", label))
    hashes: set[str] = set()
    for run in evidence.runs:
        if run.events != 10_000_000:
            issues.append(ValidationIssue("error", "BENCHMARK_EVENT_COUNT_FAILED", label))
        if run.events_per_second < minimum_rate:
            issues.append(ValidationIssue("error", "BENCHMARK_RATE_FAILED", label))
        if run.peak_rss_gib > 16.0 or run.peak_rss_gib <= 0:
            issues.append(ValidationIssue("error", "BENCHMARK_MEMORY_FAILED", label))
        if not _SHA256.fullmatch(run.artifact_sha256):
            issues.append(ValidationIssue("error", "ARTIFACT_HASH_INVALID", label))
        hashes.add(run.artifact_sha256)
    if len(hashes) != 1:
        issues.append(ValidationIssue("error", "BENCHMARK_NONDETERMINISTIC", label))
    return issues


def _market_issues(
    evidence: MarketDataEvidence,
    *,
    label: str,
    crypto: bool,
    expected_commit: str | None,
    evidence_root: Path | None,
) -> list[ValidationIssue]:
    payload, issues = _evidence_payload(evidence.evidence, label, evidence_root)
    if payload is not None:
        try:
            _exact_fields(
                payload,
                {
                    "schema_version",
                    "kind",
                    "project",
                    "source_commit",
                    "status",
                    "providers",
                    "capabilities",
                    "window_start",
                    "window_end",
                    "continuous_days",
                    "streams",
                    "quality",
                    "archive",
                },
                f"{label} evidence",
            )
            if (
                _string(payload["schema_version"], "market evidence schema_version")
                != _MARKET_EVIDENCE_SCHEMA
            ):
                raise ValueError("market evidence schema_version is unsupported")
            if _string(payload["kind"], "market evidence kind") != label:
                raise ValueError("market evidence kind does not match certification section")
            if _string(payload["project"], "market evidence project") != "quant-data-kit":
                raise ValueError("market evidence project must be quant-data-kit")
            source_commit = _string(payload["source_commit"], "market evidence source_commit")
            if expected_commit is None or source_commit != expected_commit:
                raise ValueError("market evidence source_commit does not match CI commit")
            if (
                _string(payload["status"], "market evidence status") != evidence.status
                or _string_array(payload["providers"], "market evidence providers")
                != evidence.providers
                or _string_array(payload["capabilities"], "market evidence capabilities")
                != evidence.capabilities
                or _string(payload["window_start"], "market evidence window_start")
                != evidence.window_start
                or _string(payload["window_end"], "market evidence window_end")
                != evidence.window_end
                or _integer(payload["continuous_days"], "market evidence continuous_days")
                != evidence.continuous_days
            ):
                raise ValueError("market evidence does not match certification claim")
            streams = _string_array(payload["streams"], "market evidence streams")
            if streams != tuple(sorted(set(streams))) or not streams:
                raise ValueError("market evidence streams must be non-empty, unique and sorted")
            quality = _object(payload["quality"], "market evidence quality")
            _exact_fields(
                quality,
                {
                    "raw_immutable",
                    "normalized_immutable",
                    "sequence_gaps_unexplained",
                    "quarantined_partitions",
                    "book_checkpoint_match_rate",
                    "pit_violations",
                    "cross_source_reviewed",
                },
                "market evidence quality",
            )
            archive = _object(payload["archive"], "market evidence archive")
            _exact_fields(
                archive,
                {"independent_target", "hash_verified", "restore_drill_passed", "retention_days"},
                "market evidence archive",
            )
            market_certified = evidence.status == "market-data-certified"
            if market_certified and (
                not _boolean(quality["raw_immutable"], "raw_immutable")
                or not _boolean(quality["normalized_immutable"], "normalized_immutable")
                or _integer(quality["sequence_gaps_unexplained"], "sequence_gaps_unexplained") != 0
                or _integer(quality["quarantined_partitions"], "quarantined_partitions") != 0
                or _number(quality["book_checkpoint_match_rate"], "book_checkpoint_match_rate")
                != 1.0
                or _integer(quality["pit_violations"], "pit_violations") != 0
                or not _boolean(quality["cross_source_reviewed"], "cross_source_reviewed")
                or not _boolean(archive["independent_target"], "independent_target")
                or not _boolean(archive["hash_verified"], "hash_verified")
                or not _boolean(archive["restore_drill_passed"], "restore_drill_passed")
                or _integer(archive["retention_days"], "retention_days") < 30
            ):
                raise ValueError(
                    "market-data-certified evidence has failed quality or archive gates"
                )
            if crypto and streams != _REQUIRED_CRYPTO_STREAMS:
                raise ValueError(
                    "crypto market evidence does not contain the exact frozen eight streams"
                )
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(_evidence_schema_issue(label, exc))
    if evidence.status not in {"market-data-certified", "fixture-certified"}:
        issues.append(ValidationIssue("error", "MARKET_STATUS_INVALID", label))
    if evidence.providers != tuple(sorted(set(evidence.providers))):
        issues.append(ValidationIssue("error", "MARKET_PROVIDERS_INVALID", label))
    if not evidence.providers:
        issues.append(ValidationIssue("error", "MARKET_PROVIDERS_MISSING", label))
    if evidence.capabilities != tuple(sorted(set(evidence.capabilities))):
        issues.append(ValidationIssue("error", "MARKET_CAPABILITIES_INVALID", label))
    if not evidence.capabilities:
        issues.append(ValidationIssue("error", "MARKET_CAPABILITIES_MISSING", label))
    start = _timestamp(evidence.window_start)
    end = _timestamp(evidence.window_end)
    if start is None or end is None or end <= start:
        issues.append(ValidationIssue("error", "MARKET_WINDOW_INVALID", label))
    elif evidence.continuous_days != int((end - start).total_seconds() // 86400):
        issues.append(ValidationIssue("error", "MARKET_CONTINUOUS_DAYS_MISMATCH", label))
    if crypto:
        if evidence.status != "market-data-certified":
            issues.append(ValidationIssue("error", "CRYPTO_NOT_MARKET_CERTIFIED", label))
        if set(evidence.providers) != {"binance", "okx"}:
            issues.append(ValidationIssue("error", "CRYPTO_PROVIDERS_INCOMPLETE", label))
        if not _REQUIRED_CRYPTO_CAPABILITIES.issubset(evidence.capabilities):
            issues.append(ValidationIssue("error", "CRYPTO_CAPABILITIES_INCOMPLETE", label))
        if evidence.continuous_days < 30:
            issues.append(ValidationIssue("error", "CRYPTO_WINDOW_TOO_SHORT", label))
        if start is not None and end is not None and (end - start).total_seconds() < 30 * 86400:
            issues.append(ValidationIssue("error", "CRYPTO_ELAPSED_WINDOW_TOO_SHORT", label))
    elif evidence.status == "fixture-certified":
        issues.append(
            ValidationIssue(
                "warning",
                "DOMESTIC_L2_FIXTURE_ONLY",
                "domestic L2 is not market-data-certified",
            )
        )
    else:
        if "supplier-neutral" in evidence.providers:
            issues.append(ValidationIssue("error", "DOMESTIC_PROVIDER_UNSPECIFIED", label))
        if not _REQUIRED_DOMESTIC_CAPABILITIES.issubset(evidence.capabilities):
            issues.append(ValidationIssue("error", "DOMESTIC_CAPABILITIES_INCOMPLETE", label))
        if evidence.continuous_days < 30:
            issues.append(ValidationIssue("error", "DOMESTIC_WINDOW_TOO_SHORT", label))
        if start is not None and end is not None and (end - start).total_seconds() < 30 * 86400:
            issues.append(ValidationIssue("error", "DOMESTIC_ELAPSED_WINDOW_TOO_SHORT", label))
    return issues


def _github_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=_github_headers())
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise TypeError("GitHub response root must be an object")
    return payload


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "PureSaber-quant-workspace-m7-verifier",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers=_github_headers())
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(_MAX_EVIDENCE_BYTES + 1)
    if len(body) > _MAX_EVIDENCE_BYTES:
        raise ValueError("GitHub evidence artifact exceeds the size limit")
    return body


def _bound_evidence_body(evidence: EvidenceFile, evidence_root: Path | None) -> bytes | None:
    if evidence_root is None or not _SHA256.fullmatch(evidence.sha256):
        return None
    relative = PurePosixPath(evidence.path)
    if not evidence.path or relative.is_absolute() or ".." in relative.parts:
        return None
    root = evidence_root.resolve()
    candidate = (root / Path(*relative.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    try:
        body = candidate.read_bytes()
    except OSError:
        return None
    if len(body) > _MAX_EVIDENCE_BYTES or hashlib.sha256(body).hexdigest() != evidence.sha256:
        return None
    return body


def _artifact_evidence_body(archive: bytes) -> bytes:
    try:
        with ZipFile(io.BytesIO(archive)) as bundle:
            entries = bundle.infolist()
            if (
                len(entries) != 1
                or entries[0].is_dir()
                or PurePosixPath(entries[0].filename) != PurePosixPath("evidence.json")
                or entries[0].file_size > _MAX_EVIDENCE_BYTES
            ):
                raise ValueError("evidence artifact must contain only evidence.json")
            body = bundle.read(entries[0])
    except (BadZipFile, RuntimeError) as exc:
        raise ValueError("evidence artifact is not a valid ZIP archive") from exc
    if len(body) > _MAX_EVIDENCE_BYTES:
        raise ValueError("evidence artifact entry exceeds the size limit")
    return body


def _github_ci_issues(
    item: CIResult,
    *,
    expected_evidence: dict[str, EvidenceFile],
    evidence_root: Path | None,
) -> list[ValidationIssue]:
    match = _GITHUB_RUN_URL.fullmatch(item.run_url)
    if match is None or match.group("project") != item.project:
        return [ValidationIssue("error", "CI_URL_INVALID", item.project)]
    run_id = int(match.group("run_id"))
    api_root = f"https://api.github.com/repos/PureSaber/{item.project}/actions/runs/{run_id}"
    try:
        run = _github_json(api_root)
        jobs = _github_json(f"{api_root}/jobs?per_page=100")
    except (OSError, TypeError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return [ValidationIssue("error", "CI_REMOTE_VERIFICATION_FAILED", f"{item.project}: {exc}")]
    issues: list[ValidationIssue] = []
    repository = run.get("repository")
    repository_name = repository.get("full_name") if isinstance(repository, dict) else None
    if (
        run.get("id") != run_id
        or run.get("html_url") != item.run_url
        or repository_name != f"PureSaber/{item.project}"
        or run.get("head_sha") != item.commit
        or run.get("path") != item.workflow_path
        or run.get("run_attempt") != item.run_attempt
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or run.get("event") not in {"push", "pull_request", "workflow_dispatch"}
    ):
        issues.append(ValidationIssue("error", "CI_REMOTE_RUN_MISMATCH", item.project))
    raw_jobs = jobs.get("jobs")
    if not isinstance(raw_jobs, list) or jobs.get("total_count") != len(raw_jobs):
        issues.append(ValidationIssue("error", "CI_REMOTE_JOBS_INVALID", item.project))
        return issues
    expected_names = {f"test ({version})" for version in _REQUIRED_PYTHONS}
    matched = [
        job for job in raw_jobs if isinstance(job, dict) and job.get("name") in expected_names
    ]
    if {job.get("name") for job in matched} != expected_names or len(matched) != len(
        expected_names
    ):
        issues.append(ValidationIssue("error", "CI_REMOTE_JOBS_INCOMPLETE", item.project))
    elif any(
        job.get("status") != "completed" or job.get("conclusion") != "success" for job in matched
    ):
        issues.append(ValidationIssue("error", "CI_REMOTE_JOB_FAILED", item.project))
    required_labels = _REQUIRED_EVIDENCE_BY_PROJECT.get(item.project, ())
    declared_labels = tuple(binding.label for binding in item.evidence_artifacts)
    if declared_labels != required_labels:
        issues.append(ValidationIssue("error", "CI_EVIDENCE_BINDINGS_INCOMPLETE", item.project))
        return issues
    if (
        item.workflow_path != _REQUIRED_M7_WORKFLOW_PATH
        or item.run_attempt <= 0
        or len({binding.artifact_id for binding in item.evidence_artifacts})
        != len(item.evidence_artifacts)
    ):
        issues.append(ValidationIssue("error", "CI_ATTESTATION_IDENTITY_INVALID", item.project))
        return issues
    if not required_labels:
        return issues
    try:
        artifacts = _github_json(f"{api_root}/artifacts?per_page=100")
    except (OSError, TypeError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        issues.append(
            ValidationIssue("error", "CI_EVIDENCE_ATTESTATION_FAILED", f"{item.project}: {exc}")
        )
        return issues
    raw_artifacts = artifacts.get("artifacts")
    if not isinstance(raw_artifacts, list) or artifacts.get("total_count") != len(raw_artifacts):
        issues.append(ValidationIssue("error", "CI_EVIDENCE_ARTIFACTS_INVALID", item.project))
        return issues
    by_id = {
        artifact.get("id"): artifact
        for artifact in raw_artifacts
        if isinstance(artifact, dict) and isinstance(artifact.get("id"), int)
    }
    for binding in item.evidence_artifacts:
        expected_file = expected_evidence.get(binding.label)
        local_body = (
            _bound_evidence_body(expected_file, evidence_root)
            if expected_file is not None
            else None
        )
        artifact = by_id.get(binding.artifact_id)
        expected_name = f"puresaber-m7-{binding.label}-attempt-{item.run_attempt}-evidence"
        expected_download = (
            f"https://api.github.com/repos/PureSaber/{item.project}/actions/artifacts/"
            f"{binding.artifact_id}/zip"
        )
        workflow_run = artifact.get("workflow_run") if isinstance(artifact, dict) else None
        if (
            local_body is None
            or artifact is None
            or binding.artifact_id <= 0
            or not _SHA256.fullmatch(binding.archive_sha256)
            or artifact.get("name") != expected_name
            or artifact.get("expired") is not False
            or artifact.get("digest") != f"sha256:{binding.archive_sha256}"
            or artifact.get("archive_download_url") != expected_download
            or not isinstance(workflow_run, dict)
            or workflow_run.get("id") != run_id
            or workflow_run.get("head_sha") != item.commit
        ):
            issues.append(
                ValidationIssue("error", "CI_EVIDENCE_ATTESTATION_MISMATCH", binding.label)
            )
            continue
        try:
            archive = _github_bytes(expected_download)
            if hashlib.sha256(archive).hexdigest() != binding.archive_sha256:
                raise ValueError("artifact archive digest changed")
            if _artifact_evidence_body(archive) != local_body:
                raise ValueError("artifact evidence differs from the certification evidence")
        except (OSError, TypeError, ValueError, urllib.error.URLError) as exc:
            issues.append(
                ValidationIssue(
                    "error",
                    "CI_EVIDENCE_ATTESTATION_MISMATCH",
                    f"{binding.label}: {exc}",
                )
            )
    return issues


def validate_m7_certification(
    certification: M7Certification,
    *,
    evidence_root: Path | None = None,
) -> M7ValidationResult:
    issues: list[ValidationIssue] = []
    ci_commits = {item.project: item.commit for item in certification.ci}
    evidence_by_project = {
        "quant-data-kit": {
            "data_standardization": certification.data_standardization.evidence,
            "crypto_l2": certification.crypto_l2.evidence,
            "domestic_l2": certification.domestic_l2.evidence,
        },
        "quant-execution": {
            "execution_replay": certification.execution_replay.evidence,
        },
        "quant-workspace": {},
    }
    if certification.schema_version != M7_CERTIFICATION_SCHEMA_VERSION:
        issues.append(ValidationIssue("error", "SCHEMA_VERSION_INVALID", "M7 certification"))
    created_at = _timestamp(certification.created_at)
    if created_at is None or not certification.created_at.endswith("Z"):
        issues.append(ValidationIssue("error", "CREATED_AT_INVALID", "M7 certification"))
    market_ends = tuple(
        timestamp
        for timestamp in (
            _timestamp(certification.crypto_l2.window_end),
            _timestamp(certification.domestic_l2.window_end),
        )
        if timestamp is not None
    )
    if created_at is not None and market_ends and created_at < max(market_ends):
        issues.append(ValidationIssue("error", "CERTIFICATION_PREMATURE", "M7 certification"))
    if not _SHA256.fullmatch(
        certification.certification_sha256
    ) or certification.certification_sha256 != certification_hash(certification):
        issues.append(ValidationIssue("error", "CERTIFICATION_HASH_MISMATCH", "M7 certification"))
    issues.extend(
        _benchmark_issues(
            certification.data_standardization,
            label="data_standardization",
            minimum_rate=100_000.0,
            expected_project="quant-data-kit",
            expected_commit=ci_commits.get("quant-data-kit"),
            evidence_root=evidence_root,
        )
    )
    issues.extend(
        _benchmark_issues(
            certification.execution_replay,
            label="execution_replay",
            minimum_rate=50_000.0,
            expected_project="quant-execution",
            expected_commit=ci_commits.get("quant-execution"),
            evidence_root=evidence_root,
        )
    )
    issues.extend(
        _market_issues(
            certification.crypto_l2,
            label="crypto_l2",
            crypto=True,
            expected_commit=ci_commits.get("quant-data-kit"),
            evidence_root=evidence_root,
        )
    )
    issues.extend(
        _market_issues(
            certification.domestic_l2,
            label="domestic_l2",
            crypto=False,
            expected_commit=ci_commits.get("quant-data-kit"),
            evidence_root=evidence_root,
        )
    )
    projects = tuple(item.project for item in certification.ci)
    if set(projects) != _REQUIRED_CI_PROJECTS or projects != _REQUIRED_CI_PROJECT_ORDER:
        issues.append(ValidationIssue("error", "CI_PROJECTS_INCOMPLETE", "CI"))
    for item in certification.ci:
        if not _SHA40.fullmatch(item.commit):
            issues.append(ValidationIssue("error", "CI_COMMIT_INVALID", item.project))
        if item.python_versions != _REQUIRED_PYTHONS:
            issues.append(ValidationIssue("error", "CI_PYTHON_MATRIX_INCOMPLETE", item.project))
        if item.status != "success":
            issues.append(ValidationIssue("error", "CI_FAILED", item.project))
        issues.extend(
            _github_ci_issues(
                item,
                expected_evidence=evidence_by_project.get(item.project, {}),
                evidence_root=evidence_root,
            )
        )
    errors = [issue for issue in issues if issue.severity == "error"]
    rc_ready = not errors
    ga_ready = rc_ready and certification.domestic_l2.status == "market-data-certified"
    expected_status = "ga-ready" if ga_ready else "rc-ready"
    if certification.release_status not in {"rc-ready", "ga-ready"}:
        issues.append(ValidationIssue("error", "RELEASE_STATUS_INVALID", "M7 certification"))
    elif rc_ready and certification.release_status != expected_status:
        issues.append(ValidationIssue("error", "RELEASE_STATUS_MISMATCH", "M7 certification"))
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        rc_ready = False
        ga_ready = False
    return M7ValidationResult(
        valid=not errors,
        rc_ready=rc_ready,
        ga_ready=ga_ready,
        issues=tuple(sorted(issues)),
    )


def load_m7_certification(path: Path) -> M7Certification:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"M7 certification is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise TypeError("M7 certification root must be an object")
    certification = M7Certification.from_dict(payload)
    canonical = canonical_certification_bytes(certification) + b"\n"
    if path.read_bytes() != canonical:
        raise ValueError("M7 certification bytes are not canonical")
    return certification


def write_m7_certification(path: Path, certification: M7Certification) -> None:
    result = validate_m7_certification(certification, evidence_root=path.parent)
    if not result.valid:
        codes = ", ".join(issue.code for issue in result.issues if issue.severity == "error")
        raise ValueError(f"M7 certification is not release-ready: {codes}")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = canonical_certification_bytes(certification) + b"\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise FileExistsError(f"M7 certification already exists: {path}")
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
