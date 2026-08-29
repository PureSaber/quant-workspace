"""Strict, content-addressed M7 performance and market-data certification."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal

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
class CIResult:
    project: str
    commit: str
    python_versions: tuple[str, ...]
    status: Literal["success", "failure"]
    run_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "commit": self.commit,
            "python_versions": list(self.python_versions),
            "status": self.status,
            "run_url": self.run_url,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CIResult:
        _exact_fields(
            payload,
            {"project", "commit", "python_versions", "status", "run_url"},
            "CI result",
        )
        return cls(
            project=_string(payload["project"], "CI project"),
            commit=_string(payload["commit"], "CI commit"),
            python_versions=_string_array(payload["python_versions"], "CI python_versions"),
            status=_string(payload["status"], "CI status"),  # type: ignore[arg-type]
            run_url=_string(payload["run_url"], "CI run_url"),
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _evidence_issue(
    evidence: EvidenceFile,
    label: str,
    evidence_root: Path | None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    relative = PurePosixPath(evidence.path)
    if not evidence.path or relative.is_absolute() or ".." in relative.parts:
        issues.append(ValidationIssue("error", "EVIDENCE_PATH_UNSAFE", label))
        return issues
    if not _SHA256.fullmatch(evidence.sha256):
        issues.append(ValidationIssue("error", "EVIDENCE_HASH_INVALID", label))
    if evidence_root is not None:
        root = evidence_root.resolve()
        candidate = (root / Path(*relative.parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            issues.append(ValidationIssue("error", "EVIDENCE_PATH_ESCAPE", label))
            return issues
        if not candidate.is_file():
            issues.append(ValidationIssue("error", "EVIDENCE_MISSING", label))
        elif _SHA256.fullmatch(evidence.sha256) and _sha256_file(candidate) != evidence.sha256:
            issues.append(ValidationIssue("error", "EVIDENCE_CONTENT_CHANGED", label))
    return issues


def _benchmark_issues(
    evidence: BenchmarkEvidence,
    *,
    label: str,
    minimum_rate: float,
    evidence_root: Path | None,
) -> list[ValidationIssue]:
    issues = _evidence_issue(evidence.evidence, label, evidence_root)
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
    evidence_root: Path | None,
) -> list[ValidationIssue]:
    issues = _evidence_issue(evidence.evidence, label, evidence_root)
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


def validate_m7_certification(
    certification: M7Certification,
    *,
    evidence_root: Path | None = None,
) -> M7ValidationResult:
    issues: list[ValidationIssue] = []
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
            evidence_root=evidence_root,
        )
    )
    issues.extend(
        _benchmark_issues(
            certification.execution_replay,
            label="execution_replay",
            minimum_rate=50_000.0,
            evidence_root=evidence_root,
        )
    )
    issues.extend(
        _market_issues(
            certification.crypto_l2,
            label="crypto_l2",
            crypto=True,
            evidence_root=evidence_root,
        )
    )
    issues.extend(
        _market_issues(
            certification.domestic_l2,
            label="domestic_l2",
            crypto=False,
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
        if not item.run_url.startswith(
            f"https://github.com/PureSaber/{item.project}/actions/runs/"
        ):
            issues.append(ValidationIssue("error", "CI_URL_INVALID", item.project))
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
