from __future__ import annotations

import copy
import json

import pytest
from test_delivery import create_environment, make_candidate
from test_delivery import release_fixture as base_release_fixture

from quant_workspace import delivery
from quant_workspace.cli import main
from quant_workspace.platform_status import platform_status, render_status, workflow_status


@pytest.fixture
def release_fixture(tmp_path):
    return base_release_fixture.__wrapped__(tmp_path)


def catalog():
    return {
        "schema_version": "quant.capabilities/v1",
        "as_of": "2026-10-11",
        "projects": [
            {
                "id": "sample",
                "layer": "research",
                "data_status": "synthetic",
                "assets": ["synthetic"],
                "capabilities": ["fixture"],
                "entrypoints": ["python research.py"],
                "contracts": ["test/v1"],
                "gaps": ["no market claim"],
                "evidence": ["research.py"],
            }
        ],
        "relationships": [],
        "release_scopes": {},
    }


def workflow():
    return {
        "expected_head_sha": "a" * 40,
        "required_jobs": ["acceptance (windows)", "acceptance (linux)"],
        "run": {
            "id": 12,
            "run_attempt": 2,
            "head_sha": "a" * 40,
            "status": "completed",
            "conclusion": "success",
        },
        "jobs": {
            "total_count": 2,
            "jobs": [
                {
                    "id": index + 1,
                    "run_id": 12,
                    "run_attempt": 2,
                    "head_sha": "a" * 40,
                    "name": f"acceptance ({os})",
                    "status": "completed",
                    "conclusion": "success",
                }
                for index, os in enumerate(("windows", "linux"))
            ],
        },
    }


def test_workflow_requires_complete_jobs_of_exact_attempt_and_commit():
    assert workflow_status(workflow())["status"] == "passed"
    assert workflow_status(workflow())["source_authenticated"] is False
    for field, value in (
        ("conclusion", "skipped"),
        ("conclusion", "failure"),
        ("status", "in_progress"),
        ("head_sha", "b" * 40),
        ("run_id", 13),
        ("run_attempt", 1),
    ):
        snapshot = workflow()
        snapshot["jobs"]["jobs"][0][field] = value
        assert workflow_status(snapshot)["status"] == "blocked"
    snapshot = workflow()
    snapshot["jobs"]["jobs"].pop()
    snapshot["jobs"]["total_count"] = 1
    assert workflow_status(snapshot)["required_jobs"][1]["state"] == "missing"
    snapshot = workflow()
    snapshot["run"]["head_sha"] = "b" * 40
    snapshot["run"]["status"] = "in_progress"
    assert workflow_status(snapshot)["status"] == "blocked"


@pytest.mark.parametrize(
    "change",
    ["extra", "head", "names", "run", "listing", "page", "run-id", "job", "job-id", "duplicate"],
)
def test_invalid_workflow_exports_rejected(change):
    snapshot = workflow()
    if change == "extra":
        snapshot["extra"] = True
    elif change == "head":
        snapshot["expected_head_sha"] = "main"
    elif change == "names":
        snapshot["required_jobs"] *= 2
    elif change == "run":
        snapshot["run"] = []
    elif change == "listing":
        snapshot["jobs"] = []
    elif change == "page":
        snapshot["jobs"]["total_count"] = 200
    elif change == "run-id":
        snapshot["run"]["id"] = True
    elif change == "job":
        snapshot["jobs"]["jobs"][0] = []
    elif change == "job-id":
        snapshot["jobs"]["jobs"][0]["id"] = None
    else:
        snapshot["jobs"]["jobs"][1] = copy.deepcopy(snapshot["jobs"]["jobs"][0])
    with pytest.raises((ValueError, TypeError)):
        workflow_status(snapshot)


def test_missing_source_has_no_inferred_runtime_or_certification(tmp_path):
    report = platform_status(catalog(), tmp_path)
    assert report["status"] == "attention_required"
    assert report["projects"][0]["installation"]["status"] == "not_checked"
    assert report["projects"][0]["running"]["status"] == "not_observed"
    assert report["acceptance"]["status"] == "not_run"
    assert not any(report["claims"].values())
    report["projects"][0]["id"] = "<script>alert('x')</script>"
    output = render_status(report)
    assert "<script>" not in output
    assert "&lt;script&gt;" in output


@pytest.mark.parametrize("kind", ["research", "compatibility"])
def test_real_local_acceptance_is_separate_from_running_and_current_source(
    release_fixture, tmp_path, kind
):
    fixture = release_fixture
    suite = json.loads(fixture["suite"].read_text(encoding="utf-8"))
    suite["cases"][0]["kind"] = kind
    fixture["suite"].write_text(json.dumps(suite), encoding="utf-8")
    candidate_path = tmp_path / "candidate.json"
    make_candidate(fixture, candidate_path)
    prepared = tmp_path / "prepared"
    delivery.prepare_candidate(candidate_path, fixture["root"], prepared, execute=True)
    create_environment(prepared / "sample")
    evidence_path = tmp_path / "accepted.json"
    # Real isolated interpreter, command execution, output hashes and local metadata.
    delivery.run_acceptance(candidate_path, prepared, evidence_path)
    state = tmp_path / "state"
    delivery.activate_candidate(
        candidate_path, evidence_path, prepared, state, expected_current=None
    )
    report = platform_status(
        catalog(),
        fixture["root"],
        profile_path=fixture["profile"],
        candidate_path=candidate_path,
        evidence_path=evidence_path,
        prepared_root=prepared,
        state_dir=state,
    )
    assert report["status"] == "observed"
    assert report["acceptance"]["status"] == "verified_in_combination"
    assert report["active_release"]["status"] == "pointer_recorded"
    assert report["projects"][0]["installation"]["status"] == "ready"
    assert report["projects"][0]["profile_matches_candidate"] is True
    assert report["projects"][0]["running"]["status"] == "not_observed"
    assert report["claims"]["integration_verified"] is (kind == "compatibility")
    assert report["claims"]["candidate_integration_evidence_verified"] is (kind == "compatibility")
    assert not report["claims"]["running_services_verified"]
    original = json.loads(evidence_path.read_text(encoding="utf-8"))
    for target in ("cases", "outputs", "malformed"):
        invalid = copy.deepcopy(original)
        if target == "cases":
            invalid["cases"].append(copy.deepcopy(invalid["cases"][0]))
        elif target == "outputs":
            invalid["cases"][0]["outputs"] *= 2
        else:
            invalid["cases"][0] = {"id": []}
        invalid["acceptance_sha256"] = delivery._object_hash(invalid, "acceptance_sha256")
        evidence_path.write_text(json.dumps(invalid), encoding="utf-8")
        checked = platform_status(
            catalog(),
            fixture["root"],
            candidate_path=candidate_path,
            evidence_path=evidence_path,
            prepared_root=prepared,
        )
        assert checked["acceptance"]["status"] == "blocked"
    evidence_path.write_text(json.dumps(original), encoding="utf-8")
    (prepared / "sample/result.json").write_text("tampered", encoding="utf-8")
    broken = platform_status(
        catalog(),
        fixture["root"],
        candidate_path=candidate_path,
        evidence_path=evidence_path,
        prepared_root=prepared,
    )
    assert broken["acceptance"]["status"] == "blocked"
    (prepared / "sample/result.json").write_bytes(b'{"value": 6}\n')
    (fixture["repo"] / "research.py").write_text("changed source", encoding="utf-8")
    changed = platform_status(
        catalog(),
        fixture["root"],
        profile_path=fixture["profile"],
        candidate_path=candidate_path,
        evidence_path=evidence_path,
        prepared_root=prepared,
    )
    assert changed["projects"][0]["source_matches_candidate"] is False
    assert changed["projects"][0]["installation"]["status"] == "blocked"
    assert changed["acceptance"]["status"] == "verified_in_combination"
    assert changed["claims"]["candidate_integration_evidence_verified"] is (kind == "compatibility")
    assert changed["claims"]["integration_verified"] is False


def test_status_unknown_scopes_bad_pointer_and_blocked_workflow(release_fixture, tmp_path):
    fixture = release_fixture
    unknown = catalog()
    unknown["projects"][0]["id"] = "other"
    candidate_path = tmp_path / "candidate.json"
    make_candidate(fixture, candidate_path)
    with pytest.raises(ValueError, match="outside"):
        platform_status(unknown, fixture["root"], profile_path=fixture["profile"])
    with pytest.raises(ValueError, match="outside"):
        platform_status(unknown, fixture["root"], candidate_path=candidate_path)
    with pytest.raises(ValueError, match="requires"):
        platform_status(catalog(), fixture["root"], evidence_path=tmp_path / "evidence.json")
    state = tmp_path / "state"
    report = platform_status(catalog(), fixture["root"], state_dir=state)
    assert report["active_release"]["status"] == "unmanaged"
    state.mkdir()
    (state / "current.json").write_text("{}", encoding="utf-8")
    snapshot = workflow()
    snapshot["jobs"]["jobs"][0]["conclusion"] = "skipped"
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    report = platform_status(catalog(), fixture["root"], state_dir=state, workflow_snapshot=path)
    assert report["active_release"]["status"] == "blocked"
    assert report["workflow_acceptance"]["status"] == "blocked"


def test_cli_json_html_exclusive_output_and_invalid_arguments(tmp_path, capsys):
    args = ["platform-status", "--source-root", str(tmp_path)]
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["schema_version"] == "quant.platform-status/v1"
    output = tmp_path / "status.html"
    assert main([*args, "--format", "html", "--out", str(output)]) == 2
    before = output.read_bytes()
    assert b"<table>" in before
    assert main([*args, "--format", "html", "--out", str(output)]) == 2
    assert output.read_bytes() == before
    capsys.readouterr()
    assert main([*args, "--profile", str(tmp_path / "missing")]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
