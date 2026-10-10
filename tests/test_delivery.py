from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from quant_workspace import delivery
from quant_workspace.cli import main
from quant_workspace.loader import load_workspace
from quant_workspace.runtime_readiness import create_profile, write_profile


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_distribution(
    site: Path, name: str, version: str, *, direct_url: dict | None = None
) -> None:
    metadata = site / f"{name.replace('-', '_')}-{version}.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8"
    )
    if direct_url is not None:
        (metadata / "direct_url.json").write_text(json.dumps(direct_url), encoding="utf-8")


def create_environment(repo: Path) -> None:
    environment = repo / ".venv"
    subprocess.run([sys.executable, "-I", "-m", "venv", str(environment)], check=True)
    site = environment / ("Lib/site-packages" if sys.platform == "win32" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    for metadata in site.glob("*.dist-info"):
        if not metadata.name.casefold().startswith("pip-"):
            shutil.rmtree(metadata)
    write_distribution(site, "packaging", "1.0")
    write_distribution(
        site,
        "sample",
        "0.1",
        direct_url={"url": repo.resolve().as_uri(), "dir_info": {"editable": True}},
    )


@pytest.fixture
def release_fixture(tmp_path: Path) -> dict:
    repo = tmp_path / "source" / "sample"
    repo.mkdir(parents=True)
    (repo / ".gitignore").write_text(".venv/\nresult.json\n", encoding="utf-8")
    (repo / "requirements.lock").write_text("packaging==1.0\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[project]\nname="sample"\nversion="0.1"\n', encoding="utf-8"
    )
    (repo / "research.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "raw = (json.dumps({'value': sum([1, 2, 3])}, sort_keys=True) + '\\n').encode()\n"
        "Path('result.json').write_bytes(raw)\n",
        encoding="utf-8",
    )
    git(repo, "init")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    create_environment(repo)

    config = tmp_path / "source" / "workspace.yaml"
    config.write_text("root: .\nprojects:\n  sample:\n    repo: sample\n", encoding="utf-8")
    workspace = load_workspace(config)
    profile = create_profile(
        workspace,
        ["sample"],
        python=f">={sys.version_info.major}.{sys.version_info.minor},<{sys.version_info.major}.{sys.version_info.minor + 1}",
    )
    profile_path = tmp_path / "profile.json"
    write_profile(profile_path, profile)
    result_raw = b'{"value": 6}\n'
    suite = {
        "schema_version": delivery.SUITE_SCHEMA,
        "source_revisions": {"sample": git(repo, "rev-parse", "HEAD")},
        "cases": [
            {
                "id": "synthetic-research",
                "kind": "research",
                "project": "sample",
                "cwd": "sample",
                "args": ["research.py"],
                "outputs": [{"path": "sample/result.json", "sha256": digest(result_raw)}],
            }
        ],
    }
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(suite), encoding="utf-8")
    contract_path = tmp_path / "state-v1.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema_version": delivery.STATE_SCHEMA,
                "name": "sample-state",
                "version": "1",
            }
        ),
        encoding="utf-8",
    )
    return {
        "root": tmp_path / "source",
        "repo": repo,
        "profile": profile_path,
        "suite": suite_path,
        "contract": contract_path,
    }


def make_candidate(fixture: dict, path: Path, *, contract: Path | None = None, state_dir=None):
    return delivery.create_candidate(
        fixture["profile"],
        fixture["root"],
        fixture["suite"],
        contract or fixture["contract"],
        path,
        state_dir=state_dir,
        created_at="2026-10-10T00:00:00Z",
    )


def test_release_preparation_acceptance_and_cas_activation(release_fixture, tmp_path, capsys):
    candidate_path = tmp_path / "candidate.json"
    candidate = make_candidate(release_fixture, candidate_path)
    destination = tmp_path / "prepared"
    plan = delivery.prepare_candidate(
        candidate_path, release_fixture["root"], destination, execute=False
    )
    assert plan["source_clone_network_required"] is False
    assert not destination.exists()
    assert (
        main(
            [
                "prepare-release",
                "--candidate",
                str(candidate_path),
                "--source-root",
                str(release_fixture["root"]),
                "--destination",
                str(destination),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["executed"] is False

    delivery.prepare_candidate(candidate_path, release_fixture["root"], destination, execute=True)
    create_environment(destination / "sample")
    evidence_path = tmp_path / "acceptance.json"
    evidence = delivery.run_acceptance(candidate_path, destination, evidence_path)
    assert evidence["status"] == "accepted"
    assert json.loads((destination / "sample/result.json").read_text()) == {"value": 6}

    state_dir = tmp_path / "release-state"
    activated = delivery.activate_candidate(
        candidate_path,
        evidence_path,
        destination,
        state_dir,
        expected_current=None,
    )
    assert activated["candidate_sha256"] == candidate["candidate_sha256"]
    assert activated["receipt_record"]["business_data_changed"] is False
    assert activated["manual_service_activation_required"] is True
    before = (state_dir / "current.json").read_bytes()
    with pytest.raises(ValueError, match="CAS mismatch"):
        delivery.activate_candidate(
            candidate_path,
            evidence_path,
            destination,
            state_dir,
            expected_current="0" * 64,
        )
    assert (state_dir / "current.json").read_bytes() == before


def test_state_incompatible_switch_is_blocked_without_changing_current(release_fixture, tmp_path):
    first_path = tmp_path / "candidate-v1.json"
    first = make_candidate(release_fixture, first_path)
    prepared = tmp_path / "prepared-v1"
    delivery.prepare_candidate(first_path, release_fixture["root"], prepared, execute=True)
    create_environment(prepared / "sample")
    evidence_path = tmp_path / "evidence-v1.json"
    delivery.run_acceptance(first_path, prepared, evidence_path)
    state_dir = tmp_path / "state"
    delivery.activate_candidate(first_path, evidence_path, prepared, state_dir, expected_current=None)

    contract_v2 = tmp_path / "state-v2.json"
    contract_v2.write_text(
        json.dumps(
            {
                "schema_version": delivery.STATE_SCHEMA,
                "name": "sample-state",
                "version": "2",
            }
        ),
        encoding="utf-8",
    )
    second_path = tmp_path / "candidate-v2.json"
    make_candidate(release_fixture, second_path, contract=contract_v2, state_dir=state_dir)
    prepared_v2 = tmp_path / "prepared-v2"
    delivery.prepare_candidate(second_path, release_fixture["root"], prepared_v2, execute=True)
    create_environment(prepared_v2 / "sample")
    evidence_v2 = tmp_path / "evidence-v2.json"
    delivery.run_acceptance(second_path, prepared_v2, evidence_v2)
    before = (state_dir / "current.json").read_bytes()
    with pytest.raises(ValueError, match="state compatibility"):
        delivery.activate_candidate(
            second_path,
            evidence_v2,
            prepared_v2,
            state_dir,
            expected_current=first["candidate_sha256"],
            action="rollback",
        )
    assert (state_dir / "current.json").read_bytes() == before


def test_candidate_rejects_dirty_source_commit_mismatch_and_old_environment(
    release_fixture, tmp_path
):
    (release_fixture["repo"] / "untracked.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(ValueError, match="Source does not match"):
        make_candidate(release_fixture, tmp_path / "dirty.json")
    (release_fixture["repo"] / "untracked.txt").unlink()

    suite = json.loads(release_fixture["suite"].read_text())
    suite["source_revisions"]["sample"] = "0" * 40
    bad_suite = tmp_path / "bad-suite.json"
    bad_suite.write_text(json.dumps(suite), encoding="utf-8")
    with pytest.raises(ValueError, match="does not bind"):
        delivery.create_candidate(
            release_fixture["profile"],
            release_fixture["root"],
            bad_suite,
            release_fixture["contract"],
            tmp_path / "bad-commit.json",
        )

    profile = json.loads(release_fixture["profile"].read_text())
    profile["projects"][0]["python"] = ">=99,<100"
    old_profile = tmp_path / "old-profile.json"
    old_profile.write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(ValueError, match="not ready"):
        delivery.create_candidate(
            old_profile,
            release_fixture["root"],
            release_fixture["suite"],
            release_fixture["contract"],
            tmp_path / "old.json",
        )

    shutil.rmtree(release_fixture["repo"] / ".venv")
    with pytest.raises(ValueError, match="not ready"):
        make_candidate(release_fixture, tmp_path / "missing-environment.json")


def test_acceptance_detects_missing_dependency_and_output_tampering(release_fixture, tmp_path):
    candidate_path = tmp_path / "candidate.json"
    make_candidate(release_fixture, candidate_path)
    prepared = tmp_path / "prepared"
    delivery.prepare_candidate(candidate_path, release_fixture["root"], prepared, execute=True)
    create_environment(prepared / "sample")
    site = prepared / "sample/.venv" / (
        "Lib/site-packages"
        if sys.platform == "win32"
        else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    shutil.rmtree(next(site.glob("packaging-*.dist-info")))
    with pytest.raises(ValueError, match="not ready"):
        delivery.run_acceptance(candidate_path, prepared, tmp_path / "missing-dep.json")

    write_distribution(site, "packaging", "1.0")
    evidence_path = tmp_path / "evidence.json"
    delivery.run_acceptance(candidate_path, prepared, evidence_path)
    (prepared / "sample/result.json").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="output mismatch"):
        delivery.verify_acceptance(
            delivery.load_candidate(candidate_path)[0], evidence_path, prepared
        )
