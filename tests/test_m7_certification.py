from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from quant_workspace.cli import main
from quant_workspace.m7_certification import (
    M7_CERTIFICATION_SCHEMA_VERSION,
    BenchmarkEvidence,
    BenchmarkRun,
    CIResult,
    EvidenceFile,
    M7Certification,
    MarketDataEvidence,
    canonical_certification_bytes,
    load_m7_certification,
    seal_certification,
    validate_m7_certification,
    write_m7_certification,
)


def _json_evidence(root: Path, name: str, payload: dict) -> EvidenceFile:
    path = root / name
    path.write_bytes(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    return EvidenceFile(name, hashlib.sha256(path.read_bytes()).hexdigest())


def _benchmark(
    root: Path,
    *,
    name: str,
    kind: str,
    project: str,
    commit: str,
    rate: float,
) -> BenchmarkEvidence:
    runs = tuple(
        BenchmarkRun(
            run=index,
            events=10_000_000,
            events_per_second=rate + index,
            peak_rss_gib=8.0,
            artifact_sha256="a" * 64,
        )
        for index in (1, 2, 3)
    )
    assertion_names = (
        (
            "accepted_all",
            "quarantine_zero",
            "strict_reload",
            "deterministic_artifacts",
            "pit_passed",
            "l2_checkpoints_match",
            "artifacts_retained",
        )
        if kind == "data_standardization"
        else (
            "all_events_processed",
            "order_fill_conservation",
            "ledger_balanced",
            "nav_reconciled",
            "strict_reload",
            "deterministic_artifacts",
            "artifacts_retained",
        )
    )
    payload = {
        "schema_version": "puresaber.m7-benchmark-evidence@1.0.0",
        "kind": kind,
        "project": project,
        "source_commit": commit,
        "working_tree_dirty": False,
        "measurement_scope": "full end-to-end certified path",
        "runs": [run.to_dict() for run in runs],
        "assertions": {key: True for key in assertion_names},
    }
    return BenchmarkEvidence(
        evidence=_json_evidence(root, name, payload),
        measurement_scope="full end-to-end certified path",
        runs=runs,
    )


def _market(
    root: Path,
    *,
    name: str,
    kind: str,
    status: str,
    providers: tuple[str, ...],
    capabilities: tuple[str, ...],
    window_start: str,
    window_end: str,
    continuous_days: int,
    streams: tuple[str, ...],
    commit: str,
) -> MarketDataEvidence:
    market_certified = status == "market-data-certified"
    payload = {
        "schema_version": "puresaber.m7-market-data-evidence@1.0.0",
        "kind": kind,
        "project": "quant-data-kit",
        "source_commit": commit,
        "status": status,
        "providers": list(providers),
        "capabilities": list(capabilities),
        "window_start": window_start,
        "window_end": window_end,
        "continuous_days": continuous_days,
        "streams": list(streams),
        "quality": {
            "raw_immutable": True,
            "normalized_immutable": True,
            "sequence_gaps_unexplained": 0,
            "quarantined_partitions": 0,
            "book_checkpoint_match_rate": 1.0,
            "pit_violations": 0,
            "cross_source_reviewed": True,
        },
        "archive": {
            "independent_target": market_certified,
            "hash_verified": True,
            "restore_drill_passed": True,
            "retention_days": 30 if market_certified else 1,
        },
    }
    return MarketDataEvidence(
        status=status,  # type: ignore[arg-type]
        providers=providers,
        capabilities=capabilities,
        window_start=window_start,
        window_end=window_end,
        continuous_days=continuous_days,
        evidence=_json_evidence(root, name, payload),
    )


@pytest.fixture(autouse=True)
def _trusted_github_api(monkeypatch) -> None:
    def fake_github_json(url: str) -> dict:
        run_part = url.split("/actions/runs/", 1)[1]
        run_id = int(run_part.split("/", 1)[0])
        project = url.split("/repos/PureSaber/", 1)[1].split("/", 1)[0]
        if "/jobs?" in url:
            jobs = [
                {"name": f"test ({version})", "status": "completed", "conclusion": "success"}
                for version in ("3.10", "3.11", "3.12")
            ]
            return {"total_count": len(jobs), "jobs": jobs}
        return {
            "id": run_id,
            "html_url": f"https://github.com/PureSaber/{project}/actions/runs/{run_id}",
            "repository": {"full_name": f"PureSaber/{project}"},
            "head_sha": str(run_id) * 40,
            "status": "completed",
            "conclusion": "success",
            "event": "push",
        }

    monkeypatch.setattr("quant_workspace.m7_certification._github_json", fake_github_json)


def _certification(root: Path, *, domestic_status: str = "fixture-certified") -> M7Certification:
    data_commit = "1" * 40
    execution_commit = "2" * 40
    data = _benchmark(
        root,
        name="data.json",
        kind="data_standardization",
        project="quant-data-kit",
        commit=data_commit,
        rate=100_000,
    )
    execution = _benchmark(
        root,
        name="execution.json",
        kind="execution_replay",
        project="quant-execution",
        commit=execution_commit,
        rate=50_000,
    )
    crypto = _market(
        root,
        name="crypto.json",
        kind="crypto_l2",
        status="market-data-certified",
        providers=("binance", "okx"),
        capabilities=(
            "btc-spot-l2",
            "btc-usdt-perpetual-l2",
            "eth-spot-l2",
            "eth-usdt-perpetual-l2",
        ),
        window_start="2026-07-01T00:00:00Z",
        window_end="2026-07-31T00:00:00Z",
        continuous_days=30,
        streams=(
            "binance:spot:BTCUSDT",
            "binance:spot:ETHUSDT",
            "binance:usdt-perpetual:BTCUSDT",
            "binance:usdt-perpetual:ETHUSDT",
            "okx:spot:BTC-USDT",
            "okx:spot:ETH-USDT",
            "okx:usdt-perpetual:BTC-USDT-SWAP",
            "okx:usdt-perpetual:ETH-USDT-SWAP",
        ),
        commit=data_commit,
    )
    domestic = _market(
        root,
        name="domestic.json",
        kind="domestic_l2",
        status=domestic_status,  # type: ignore[arg-type]
        providers=(
            ("licensed-domestic-provider",)
            if domestic_status == "market-data-certified"
            else ("supplier-neutral",)
        ),
        capabilities=("domestic-l2-replay",),
        window_start="2026-07-01T00:00:00Z",
        window_end=(
            "2026-07-31T00:00:00Z"
            if domestic_status == "market-data-certified"
            else "2026-07-02T00:00:00Z"
        ),
        continuous_days=30 if domestic_status == "market-data-certified" else 1,
        streams=(
            ("licensed-domestic-provider:market:l2",)
            if domestic_status == "market-data-certified"
            else ("supplier-neutral:fixture:domestic-l2",)
        ),
        commit=data_commit,
    )
    ci = tuple(
        CIResult(
            project=project,
            commit=str(index) * 40,
            python_versions=("3.10", "3.11", "3.12"),
            status="success",
            run_url=f"https://github.com/PureSaber/{project}/actions/runs/{index}",
        )
        for index, project in enumerate(
            ("quant-data-kit", "quant-execution", "quant-workspace"), start=1
        )
    )
    return seal_certification(
        M7Certification(
            schema_version=M7_CERTIFICATION_SCHEMA_VERSION,
            created_at="2026-08-29T12:00:00Z",
            data_standardization=data,
            execution_replay=execution,
            crypto_l2=crypto,
            domestic_l2=domestic,
            ci=ci,
            release_status=(
                "ga-ready" if domestic_status == "market-data-certified" else "rc-ready"
            ),
            certification_sha256="",
        )
    )


def _codes(certification: M7Certification, root: Path) -> set[str]:
    return {
        item.code for item in validate_m7_certification(certification, evidence_root=root).issues
    }


def test_rc_and_ga_certifications_are_strict_and_content_addressed(tmp_path: Path) -> None:
    rc = _certification(tmp_path)
    rc_result = validate_m7_certification(rc, evidence_root=tmp_path)
    assert rc_result.valid and rc_result.rc_ready and not rc_result.ga_ready
    assert [item.code for item in rc_result.issues] == ["DOMESTIC_L2_FIXTURE_ONLY"]

    ga_root = tmp_path / "ga"
    ga_root.mkdir()
    ga = _certification(ga_root, domestic_status="market-data-certified")
    ga_result = validate_m7_certification(ga, evidence_root=ga_root)
    assert ga_result.to_dict() == {
        "valid": True,
        "rc_ready": True,
        "ga_ready": True,
        "issues": [],
    }


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda item: replace(
                item,
                data_standardization=replace(
                    item.data_standardization,
                    runs=(replace(item.data_standardization.runs[0], events=9_999_999),)
                    + item.data_standardization.runs[1:],
                ),
            ),
            "BENCHMARK_EVENT_COUNT_FAILED",
        ),
        (
            lambda item: replace(
                item,
                execution_replay=replace(
                    item.execution_replay,
                    runs=(replace(item.execution_replay.runs[0], events_per_second=49_999),)
                    + item.execution_replay.runs[1:],
                ),
            ),
            "BENCHMARK_RATE_FAILED",
        ),
        (
            lambda item: replace(
                item,
                data_standardization=replace(
                    item.data_standardization,
                    runs=(replace(item.data_standardization.runs[0], peak_rss_gib=16.1),)
                    + item.data_standardization.runs[1:],
                ),
            ),
            "BENCHMARK_MEMORY_FAILED",
        ),
        (
            lambda item: replace(
                item,
                crypto_l2=replace(item.crypto_l2, continuous_days=29),
            ),
            "CRYPTO_WINDOW_TOO_SHORT",
        ),
        (
            lambda item: replace(
                item,
                crypto_l2=replace(item.crypto_l2, providers=("binance",)),
            ),
            "CRYPTO_PROVIDERS_INCOMPLETE",
        ),
        (
            lambda item: replace(item, ci=item.ci[:2]),
            "CI_PROJECTS_INCOMPLETE",
        ),
    ],
)
def test_release_gates_fail_closed(tmp_path: Path, mutate, code: str) -> None:
    changed = seal_certification(mutate(_certification(tmp_path)))
    assert code in _codes(changed, tmp_path)
    assert not validate_m7_certification(changed, evidence_root=tmp_path).rc_ready


def test_determinism_and_evidence_tampering_fail(tmp_path: Path) -> None:
    certification = _certification(tmp_path)
    nondeterministic = replace(certification.data_standardization.runs[2], artifact_sha256="b" * 64)
    changed = seal_certification(
        replace(
            certification,
            data_standardization=replace(
                certification.data_standardization,
                runs=certification.data_standardization.runs[:2] + (nondeterministic,),
            ),
        )
    )
    assert "BENCHMARK_NONDETERMINISTIC" in _codes(changed, tmp_path)

    (tmp_path / "data.json").write_text("tampered", encoding="utf-8")
    assert "EVIDENCE_CONTENT_CHANGED" in _codes(certification, tmp_path)


def test_rehashed_arbitrary_or_semantically_mismatched_evidence_fails(tmp_path: Path) -> None:
    certification = _certification(tmp_path)
    path = tmp_path / "data.json"

    path.write_bytes(b"arbitrary evidence\n")
    arbitrary = EvidenceFile("data.json", hashlib.sha256(path.read_bytes()).hexdigest())
    changed = seal_certification(
        replace(
            certification,
            data_standardization=replace(
                certification.data_standardization,
                evidence=arbitrary,
            ),
        )
    )
    assert "EVIDENCE_JSON_INVALID" in _codes(changed, tmp_path)
    assert not validate_m7_certification(changed, evidence_root=tmp_path).rc_ready

    certification = _certification(tmp_path)
    payload = json.loads(path.read_bytes())
    payload["project"] = "forged-project"
    forged = _json_evidence(tmp_path, "data.json", payload)
    changed = seal_certification(
        replace(
            certification,
            data_standardization=replace(
                certification.data_standardization,
                evidence=forged,
            ),
        )
    )
    assert "EVIDENCE_SCHEMA_INVALID" in _codes(changed, tmp_path)
    assert not validate_m7_certification(changed, evidence_root=tmp_path).rc_ready


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "unsupported"),
        ("kind", "execution_replay"),
        ("project", "forged-project"),
        ("source_commit", "invalid"),
        ("working_tree_dirty", True),
        ("measurement_scope", "partial path"),
        ("runs", []),
        ("assertions.accepted_all", False),
        ("assertions.accepted_all", "yes"),
    ],
)
def test_benchmark_evidence_schema_and_claims_fail_closed(
    tmp_path: Path,
    field: str,
    value,
) -> None:
    certification = _certification(tmp_path)
    path = tmp_path / "data.json"
    payload = json.loads(path.read_bytes())
    if field.startswith("assertions."):
        payload["assertions"][field.split(".", 1)[1]] = value
    else:
        payload[field] = value
    forged = _json_evidence(tmp_path, "data.json", payload)
    changed = seal_certification(
        replace(
            certification,
            data_standardization=replace(certification.data_standardization, evidence=forged),
        )
    )
    assert "EVIDENCE_SCHEMA_INVALID" in _codes(changed, tmp_path)
    assert not validate_m7_certification(changed, evidence_root=tmp_path).rc_ready


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "unsupported"),
        ("kind", "domestic_l2"),
        ("project", "forged-project"),
        ("source_commit", "f" * 40),
        ("providers", ["binance"]),
        ("streams", []),
        ("streams", ["binance:spot:BTCUSDT", "binance:spot:BTCUSDT"]),
        ("quality.raw_immutable", False),
        ("archive.retention_days", 1),
    ],
)
def test_market_evidence_schema_quality_and_streams_fail_closed(
    tmp_path: Path,
    field: str,
    value,
) -> None:
    certification = _certification(tmp_path)
    path = tmp_path / "crypto.json"
    payload = json.loads(path.read_bytes())
    if "." in field:
        section, name = field.split(".", 1)
        payload[section][name] = value
    else:
        payload[field] = value
    forged = _json_evidence(tmp_path, "crypto.json", payload)
    changed = seal_certification(
        replace(
            certification,
            crypto_l2=replace(certification.crypto_l2, evidence=forged),
        )
    )
    assert "EVIDENCE_SCHEMA_INVALID" in _codes(changed, tmp_path)
    assert not validate_m7_certification(changed, evidence_root=tmp_path).rc_ready


def test_non_object_and_noncanonical_evidence_fail_closed(tmp_path: Path) -> None:
    certification = _certification(tmp_path)
    path = tmp_path / "data.json"
    path.write_bytes(b"[]\n")
    evidence = EvidenceFile("data.json", hashlib.sha256(path.read_bytes()).hexdigest())
    changed = seal_certification(
        replace(
            certification,
            data_standardization=replace(certification.data_standardization, evidence=evidence),
        )
    )
    assert "EVIDENCE_JSON_INVALID" in _codes(changed, tmp_path)

    certification = _certification(tmp_path)
    path.write_text(json.dumps(json.loads(path.read_bytes()), indent=2), encoding="utf-8")
    evidence = EvidenceFile("data.json", hashlib.sha256(path.read_bytes()).hexdigest())
    changed = seal_certification(
        replace(
            certification,
            data_standardization=replace(certification.data_standardization, evidence=evidence),
        )
    )
    assert "EVIDENCE_NOT_CANONICAL" in _codes(changed, tmp_path)


def test_remote_ci_head_and_job_matrix_are_verified(tmp_path: Path, monkeypatch) -> None:
    certification = _certification(tmp_path)

    def forged_github_json(url: str) -> dict:
        if "/jobs?" in url:
            return {
                "total_count": 1,
                "jobs": [{"name": "test (3.12)", "status": "completed", "conclusion": "success"}],
            }
        run_part = url.split("/actions/runs/", 1)[1]
        run_id = int(run_part.split("/", 1)[0])
        project = url.split("/repos/PureSaber/", 1)[1].split("/", 1)[0]
        return {
            "id": run_id,
            "html_url": f"https://github.com/PureSaber/{project}/actions/runs/{run_id}",
            "repository": {"full_name": f"PureSaber/{project}"},
            "head_sha": "f" * 40,
            "status": "completed",
            "conclusion": "success",
            "event": "push",
        }

    monkeypatch.setattr("quant_workspace.m7_certification._github_json", forged_github_json)
    codes = _codes(certification, tmp_path)
    assert "CI_REMOTE_RUN_MISMATCH" in codes
    assert "CI_REMOTE_JOBS_INCOMPLETE" in codes
    assert not validate_m7_certification(certification, evidence_root=tmp_path).rc_ready


def test_remote_ci_transport_invalid_jobs_and_failed_job_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    certification = _certification(tmp_path)

    def unavailable(_url: str) -> dict:
        raise OSError("offline")

    monkeypatch.setattr("quant_workspace.m7_certification._github_json", unavailable)
    assert "CI_REMOTE_VERIFICATION_FAILED" in _codes(certification, tmp_path)

    def invalid_jobs(url: str) -> dict:
        if "/jobs?" in url:
            return {"total_count": 2, "jobs": []}
        run_id = int(url.split("/actions/runs/", 1)[1].split("/", 1)[0])
        project = url.split("/repos/PureSaber/", 1)[1].split("/", 1)[0]
        return {
            "id": run_id,
            "html_url": f"https://github.com/PureSaber/{project}/actions/runs/{run_id}",
            "repository": {"full_name": f"PureSaber/{project}"},
            "head_sha": str(run_id) * 40,
            "status": "completed",
            "conclusion": "success",
            "event": "push",
        }

    monkeypatch.setattr("quant_workspace.m7_certification._github_json", invalid_jobs)
    assert "CI_REMOTE_JOBS_INVALID" in _codes(certification, tmp_path)

    def failed_job(url: str) -> dict:
        payload = _trusted_payload(url)
        if "/jobs?" in url:
            payload["jobs"][0]["conclusion"] = "failure"
        return payload

    def _trusted_payload(url: str) -> dict:
        run_id = int(url.split("/actions/runs/", 1)[1].split("/", 1)[0])
        project = url.split("/repos/PureSaber/", 1)[1].split("/", 1)[0]
        if "/jobs?" in url:
            jobs = [
                {"name": f"test ({version})", "status": "completed", "conclusion": "success"}
                for version in ("3.10", "3.11", "3.12")
            ]
            return {"total_count": len(jobs), "jobs": jobs}
        return {
            "id": run_id,
            "html_url": f"https://github.com/PureSaber/{project}/actions/runs/{run_id}",
            "repository": {"full_name": f"PureSaber/{project}"},
            "head_sha": str(run_id) * 40,
            "status": "completed",
            "conclusion": "success",
            "event": "push",
        }

    monkeypatch.setattr("quant_workspace.m7_certification._github_json", failed_job)
    assert "CI_REMOTE_JOB_FAILED" in _codes(certification, tmp_path)


def test_write_load_cli_and_no_clobber(tmp_path: Path, capsys) -> None:
    certification = _certification(tmp_path)
    path = tmp_path / "certification.json"
    write_m7_certification(path, certification)
    assert path.read_bytes() == canonical_certification_bytes(certification) + b"\n"
    assert load_m7_certification(path) == certification
    with pytest.raises(FileExistsError):
        write_m7_certification(path, certification)

    assert main(["verify-m7-certification", str(path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["valid"] is True and output["rc_ready"] is True


def test_noncanonical_unknown_and_unsafe_evidence_are_rejected(tmp_path: Path) -> None:
    certification = _certification(tmp_path)
    path = tmp_path / "noncanonical.json"
    path.write_text(json.dumps(certification.to_dict(), indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="not canonical"):
        load_m7_certification(path)

    payload = certification.to_dict()
    payload["unknown"] = True
    with pytest.raises(ValueError, match="unknown"):
        M7Certification.from_dict(payload)

    unsafe = seal_certification(
        replace(
            certification,
            data_standardization=replace(
                certification.data_standardization,
                evidence=EvidenceFile("../escape.json", "a" * 64),
            ),
        )
    )
    assert "EVIDENCE_PATH_UNSAFE" in _codes(unsafe, tmp_path)


def test_from_dict_rejects_invalid_scalar_types_and_values(tmp_path: Path) -> None:
    payload = _certification(tmp_path).to_dict()
    payload["data_standardization"]["runs"][0]["run"] = True
    with pytest.raises(ValueError, match="must be an integer"):
        M7Certification.from_dict(payload)

    payload = _certification(tmp_path).to_dict()
    payload["execution_replay"]["runs"][0]["events_per_second"] = "fast"
    with pytest.raises(ValueError, match="must be numeric"):
        M7Certification.from_dict(payload)

    payload = _certification(tmp_path).to_dict()
    payload["execution_replay"]["runs"][0]["peak_rss_gib"] = float("inf")
    with pytest.raises(ValueError, match="must be finite"):
        M7Certification.from_dict(payload)

    payload = _certification(tmp_path).to_dict()
    payload["crypto_l2"]["providers"] = "binance"
    with pytest.raises(ValueError, match="providers must be an array"):
        M7Certification.from_dict(payload)

    payload = _certification(tmp_path).to_dict()
    payload["data_standardization"]["evidence"]["path"] = 1
    with pytest.raises(ValueError, match="evidence path must be a string"):
        M7Certification.from_dict(payload)

    payload = _certification(tmp_path).to_dict()
    payload["ci"] = ["not-an-object"]
    with pytest.raises(ValueError, match="CI result must be an object"):
        M7Certification.from_dict(payload)


def test_all_benchmark_and_evidence_fail_closed_branches(tmp_path: Path) -> None:
    certification = _certification(tmp_path)
    missing = EvidenceFile("missing.json", "not-a-sha")
    runs = (
        replace(
            certification.data_standardization.runs[0],
            run=4,
            peak_rss_gib=0,
            artifact_sha256="invalid",
        ),
    )
    changed = seal_certification(
        replace(
            certification,
            data_standardization=replace(
                certification.data_standardization,
                evidence=missing,
                measurement_scope=" ",
                runs=runs,
            ),
        )
    )
    assert {
        "EVIDENCE_HASH_INVALID",
        "EVIDENCE_MISSING",
        "MEASUREMENT_SCOPE_MISSING",
        "BENCHMARK_RUNS_INVALID",
        "BENCHMARK_MEMORY_FAILED",
        "ARTIFACT_HASH_INVALID",
    }.issubset(_codes(changed, tmp_path))
    assert "EVIDENCE_MISSING" not in {
        item.code for item in validate_m7_certification(changed).issues
    }


def test_market_data_and_release_metadata_fail_closed_branches(tmp_path: Path) -> None:
    certification = _certification(tmp_path)
    broken_market = replace(
        certification.crypto_l2,
        status="fixture-certified",  # type: ignore[arg-type]
        providers=("binance", "binance"),
        capabilities=(),
        window_start="not-a-time",
        window_end="2026-07-02T00:00:00",
        continuous_days=1,
    )
    broken_ci = tuple(
        replace(
            item,
            commit="bad",
            python_versions=("3.12",),
            status="failure",
            run_url="http://invalid.example",
        )
        for item in certification.ci
    )
    changed = seal_certification(
        replace(
            certification,
            schema_version="2.0.0",
            created_at="2026-08-29T12:00:00",
            crypto_l2=broken_market,
            ci=broken_ci,
            release_status="invalid",  # type: ignore[arg-type]
        )
    )
    assert {
        "SCHEMA_VERSION_INVALID",
        "CREATED_AT_INVALID",
        "MARKET_PROVIDERS_INVALID",
        "MARKET_CAPABILITIES_MISSING",
        "MARKET_WINDOW_INVALID",
        "CRYPTO_NOT_MARKET_CERTIFIED",
        "CRYPTO_PROVIDERS_INCOMPLETE",
        "CRYPTO_CAPABILITIES_INCOMPLETE",
        "CRYPTO_WINDOW_TOO_SHORT",
        "CI_COMMIT_INVALID",
        "CI_PYTHON_MATRIX_INCOMPLETE",
        "CI_FAILED",
        "CI_URL_INVALID",
        "RELEASE_STATUS_INVALID",
    }.issubset(_codes(changed, tmp_path))

    mismatch = seal_certification(replace(certification, release_status="ga-ready"))
    assert "RELEASE_STATUS_MISMATCH" in _codes(mismatch, tmp_path)

    noncanonical = seal_certification(
        replace(
            certification,
            created_at="2026-06-01T00:00:00Z",
            crypto_l2=replace(
                certification.crypto_l2,
                providers=("okx", "binance"),
                capabilities=tuple(reversed(certification.crypto_l2.capabilities)),
                continuous_days=31,
            ),
            ci=tuple(reversed(certification.ci)),
        )
    )
    assert {
        "CERTIFICATION_PREMATURE",
        "MARKET_PROVIDERS_INVALID",
        "MARKET_CAPABILITIES_INVALID",
        "MARKET_CONTINUOUS_DAYS_MISMATCH",
        "CI_PROJECTS_INCOMPLETE",
    }.issubset(_codes(noncanonical, tmp_path))


def test_domestic_market_certification_requires_30_days_and_capability(tmp_path: Path) -> None:
    certification = _certification(tmp_path, domestic_status="market-data-certified")
    changed = seal_certification(
        replace(
            certification,
            domestic_l2=replace(
                certification.domestic_l2,
                providers=("supplier-neutral",),
                capabilities=(),
                window_end="2026-07-02T00:00:00Z",
                continuous_days=1,
            ),
        )
    )
    assert {
        "MARKET_CAPABILITIES_MISSING",
        "DOMESTIC_PROVIDER_UNSPECIFIED",
        "DOMESTIC_CAPABILITIES_INCOMPLETE",
        "DOMESTIC_WINDOW_TOO_SHORT",
        "DOMESTIC_ELAPSED_WINDOW_TOO_SHORT",
    }.issubset(_codes(changed, tmp_path))
    assert not validate_m7_certification(changed, evidence_root=tmp_path).ga_ready

    missing_provider = seal_certification(
        replace(
            certification,
            domestic_l2=replace(certification.domestic_l2, providers=()),
        )
    )
    assert "MARKET_PROVIDERS_MISSING" in _codes(missing_provider, tmp_path)


def test_hash_timestamp_loader_and_writer_rejections(tmp_path: Path) -> None:
    certification = _certification(tmp_path)
    changed = replace(certification, certification_sha256="0" * 64)
    assert "CERTIFICATION_HASH_MISMATCH" in _codes(changed, tmp_path)

    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        load_m7_certification(path)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(TypeError, match="root must be an object"):
        load_m7_certification(path)

    with pytest.raises(ValueError, match="not release-ready"):
        write_m7_certification(tmp_path / "invalid.json", changed)
