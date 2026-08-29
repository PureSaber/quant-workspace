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


def _evidence(root: Path, name: str) -> EvidenceFile:
    path = root / name
    path.write_bytes(name.encode())
    return EvidenceFile(name, hashlib.sha256(path.read_bytes()).hexdigest())


def _benchmark(evidence: EvidenceFile, rate: float) -> BenchmarkEvidence:
    return BenchmarkEvidence(
        evidence=evidence,
        measurement_scope="full end-to-end certified path",
        runs=tuple(
            BenchmarkRun(
                run=index,
                events=10_000_000,
                events_per_second=rate + index,
                peak_rss_gib=8.0,
                artifact_sha256="a" * 64,
            )
            for index in (1, 2, 3)
        ),
    )


def _certification(root: Path, *, domestic_status: str = "fixture-certified") -> M7Certification:
    data = _benchmark(_evidence(root, "data.json"), 100_000)
    execution = _benchmark(_evidence(root, "execution.json"), 50_000)
    crypto = MarketDataEvidence(
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
        evidence=_evidence(root, "crypto.json"),
    )
    domestic = MarketDataEvidence(
        status=domestic_status,  # type: ignore[arg-type]
        providers=("supplier-neutral",),
        capabilities=("domestic-l2-replay",),
        window_start="2026-07-01T00:00:00Z",
        window_end=(
            "2026-07-31T00:00:00Z"
            if domestic_status == "market-data-certified"
            else "2026-07-02T00:00:00Z"
        ),
        continuous_days=30 if domestic_status == "market-data-certified" else 1,
        evidence=_evidence(root, "domestic.json"),
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


def test_domestic_market_certification_requires_30_days_and_capability(tmp_path: Path) -> None:
    certification = _certification(tmp_path, domestic_status="market-data-certified")
    changed = seal_certification(
        replace(
            certification,
            domestic_l2=replace(
                certification.domestic_l2,
                providers=(),
                capabilities=(),
                window_end="2026-07-02T00:00:00Z",
                continuous_days=1,
            ),
        )
    )
    assert {
        "MARKET_PROVIDERS_MISSING",
        "MARKET_CAPABILITIES_MISSING",
        "DOMESTIC_CAPABILITIES_INCOMPLETE",
        "DOMESTIC_WINDOW_TOO_SHORT",
        "DOMESTIC_ELAPSED_WINDOW_TOO_SHORT",
    }.issubset(_codes(changed, tmp_path))
    assert not validate_m7_certification(changed, evidence_root=tmp_path).ga_ready


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
