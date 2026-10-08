from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from quant_workspace import runtime_readiness as runtime
from quant_workspace.cli import main
from quant_workspace.loader import load_workspace


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "sample"
    repo.mkdir()
    (repo / ".gitignore").write_text(".venv/\n")
    (repo / "requirements.lock").write_text("packaging==26.3\n")
    (repo / "pyproject.toml").write_text('[project]\nname="sample"\nversion="0.1"\n')
    git(repo, "init")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    config = tmp_path / "workspace.yaml"
    config.write_text("root: .\nprojects:\n  sample:\n    repo: sample\n")
    workspace = load_workspace(config)
    profile = runtime.create_profile(workspace, ["sample"], python=">=3.10,<4")
    path = tmp_path / "runtime.json"
    runtime.write_profile(path, profile)
    return repo, path, workspace


def probe(repo, version="26.3"):
    return {
        "prefix": str(repo / ".venv"),
        "base_prefix": str(repo / "base"),
        "version": "3.12.14",
        "marker_environment": {"python_version": "3.12", "extra": ""},
        "distributions": [
            {"name": "packaging", "version": version, "requires": [], "direct_url": None},
            {
                "name": "sample",
                "version": "0.1",
                "requires": ["packaging>=23"],
                "direct_url": {"url": repo.as_uri(), "dir_info": {"editable": True}},
            },
        ],
    }


def prepare_probe(monkeypatch, repo, value=None):
    executable = runtime.python_path(repo / ".venv")
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(runtime, "_probe", lambda *args: value or probe(repo))


def test_profile_pins_clean_source_and_lock_without_runtime_claim(checkout):
    repo, path, workspace = checkout
    item = json.loads(path.read_text())["projects"][0]
    assert item["revision"] == git(repo, "rev-parse", "HEAD")
    assert item["lock_sha256"] == runtime.digest((repo / "requirements.lock").read_bytes())
    report = runtime.check_runtime(path, workspace.root)
    assert report["status"] == "blocked"
    assert report["projects"][0]["issues"] == ["environment_missing"]
    assert not report["claims"]["integration_verified"]
    with pytest.raises(FileExistsError):
        runtime.write_profile(path, {})


def test_ready_probe_checks_editable_source_and_lock(monkeypatch, checkout):
    repo, path, workspace = checkout
    prepare_probe(monkeypatch, repo)
    before = git(repo, "status", "--porcelain")
    report = runtime.check_runtime(path, workspace.root)
    assert report["status"] == "ready"
    assert report["projects"][0]["issues"] == []
    assert report["claims"]["market_data_certified"] is False
    assert git(repo, "status", "--porcelain") == before


@pytest.mark.parametrize(
    "mutation,expected",
    [
        (lambda p: p.update(version="3.9.1"), "python_version_mismatch"),
        (lambda p: p.update(prefix="/wrong"), "environment_identity_mismatch"),
        (lambda p: p["distributions"][0].update(version="20"), "locked_version_mismatch:packaging"),
        (lambda p: p["distributions"][1].update(direct_url=None), "editable_source_mismatch"),
        (
            lambda p: p["distributions"][1].update(requires=["absent>=1"]),
            "missing_dependency:sample:absent",
        ),
        (
            lambda p: p["distributions"].append(p["distributions"][0]),
            "duplicate_distribution:packaging",
        ),
    ],
)
def test_environment_mismatch_blocks(monkeypatch, checkout, mutation, expected):
    repo, path, workspace = checkout
    value = probe(repo)
    mutation(value)
    prepare_probe(monkeypatch, repo, value)
    result = runtime.check_runtime(path, workspace.root)
    assert result["status"] == "blocked"
    assert expected in result["projects"][0]["issues"]


def test_dirty_and_wrong_revision_do_not_invoke_python(monkeypatch, checkout):
    repo, path, workspace = checkout
    monkeypatch.setattr(runtime, "_probe", lambda *a: pytest.fail("untrusted checkout executed"))
    (repo / "requirements.lock").write_text("packaging==20\n")
    result = runtime.check_runtime(path, workspace.root)
    assert {"dirty_checkout", "lock_mismatch"} <= set(result["projects"][0]["issues"])
    git(repo, "add", ".")
    git(repo, "commit", "-m", "changed")
    assert (
        "revision_mismatch" in runtime.check_runtime(path, workspace.root)["projects"][0]["issues"]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("repo", "../escape"),
        ("environment", "../escape"),
        ("lock", "/absolute"),
        ("revision", "main"),
    ],
)
def test_invalid_profile_is_rejected(checkout, field, value):
    _, path, workspace = checkout
    payload = json.loads(path.read_text())
    payload["projects"][0][field] = value
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        runtime.check_runtime(path, workspace.root)


def test_bootstrap_is_preview_first_and_never_reuses_environment(checkout):
    repo, path, workspace = checkout
    plan = runtime.bootstrap(path, workspace.root, "sample")
    assert plan["executed"] is False and not (repo / ".venv").exists()
    assert plan["commands"][1][-2:] == ["-r", str(repo / "requirements.lock")]
    (repo / ".venv").mkdir()
    with pytest.raises(ValueError, match="already exists"):
        runtime.bootstrap(path, workspace.root, "sample", execute=True)


def test_lock_parser_handles_markers_hashes_and_rejects_unpinned():
    rows = runtime.lock_requirements(
        "# comment\nfoo==1 "
        + "\\"
        + "\n --hash=sha256:"
        + "a" * 64
        + "\nbar==2 ; python_version < '3.11'\n"
    )
    assert [r.name for r in rows] == ["foo", "bar"]
    for text in ["foo>=1", "-e .", "-r other.txt", "foo @ https://example.invalid/pkg.whl"]:
        with pytest.raises(ValueError, match="locked"):
            runtime.lock_requirements(text)


def test_actual_isolated_probe_collects_metadata():
    result = runtime._probe(Path(sys.executable), Path.cwd())
    assert result["version"].startswith("3.")
    assert any(d["name"].lower() == "packaging" for d in result["distributions"])


def test_git_lock_commit_and_provider_must_match(monkeypatch, checkout):
    repo, path, workspace = checkout
    sha = "a" * 40
    requirement = f"dependency @ git+https://example.invalid/dependency.git@{sha}\n"
    (repo / "requirements.lock").write_text(requirement)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "git dependency")
    profile = runtime.create_profile(workspace, ["sample"], python=">=3.10,<4")
    path.write_text(json.dumps(profile))
    value = probe(repo)
    value["distributions"].append(
        {
            "name": "dependency",
            "version": "1",
            "requires": [],
            "direct_url": {
                "url": "https://example.invalid/dependency.git",
                "vcs_info": {"commit_id": sha},
            },
        }
    )
    prepare_probe(monkeypatch, repo, value)
    assert runtime.check_runtime(path, workspace.root)["status"] == "ready"
    value["distributions"][-1]["direct_url"]["url"] = "https://other.invalid/dependency.git"
    assert (
        "locked_git_mismatch:dependency"
        in runtime.check_runtime(path, workspace.root)["projects"][0]["issues"]
    )


def test_marker_packages_not_required_but_incompatible_active_dependency_blocks(
    monkeypatch, checkout
):
    repo, path, workspace = checkout
    value = probe(repo)
    value["distributions"][1]["requires"] = ["absent; python_version < '3.10'", "packaging<20"]
    prepare_probe(monkeypatch, repo, value)
    result = runtime.check_runtime(path, workspace.root)
    assert result["projects"][0]["issues"] == ["incompatible_dependency:sample:packaging"]


def test_bootstrap_executes_fixed_argv_and_preserves_failure_logs(monkeypatch, checkout):
    repo, path, workspace = checkout
    real_source = runtime._source
    monkeypatch.setattr(runtime, "_source", lambda *a: real_source(*a))
    commands = []

    def invoke(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 1, "partial install", "failure")

    # Source inspection also uses subprocess.run; preserve that read-only path.
    real_run = subprocess.run
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda c, **k: real_run(c, **k) if c[0] == "git" else invoke(c, **k),
    )
    with pytest.raises(ValueError, match="incomplete environment retained"):
        runtime.bootstrap(path, workspace.root, "sample", execute=True)
    assert len(commands) == 1
    assert (repo / ".venv/bootstrap-0.log").read_text() == "partial installfailure"


def test_bootstrap_success_verifies_selected_environment(monkeypatch, checkout):
    repo, path, workspace = checkout
    real_run = subprocess.run

    def invoke(command, **kwargs):
        if command[0] == "git":
            return real_run(command, **kwargs)
        target = runtime.python_path(repo / ".venv")
        target.parent.mkdir(exist_ok=True)
        target.touch()
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(runtime.subprocess, "run", invoke)
    monkeypatch.setattr(runtime, "_probe", lambda *a: probe(repo))
    assert runtime.bootstrap(path, workspace.root, "sample", execute=True)["executed"]
    assert len(list((repo / ".venv").glob("bootstrap-*.log"))) == 4


def test_cli_reports_missing_environment_without_writes(checkout, capsys):
    _, path, workspace = checkout
    assert main(["--config", str(workspace.config_path), "doctor", "--profile", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    assert (
        main(
            [
                "--config",
                str(workspace.config_path),
                "bootstrap-env",
                "--profile",
                str(path),
                "--project",
                "missing",
            ]
        )
        == 2
    )
    assert "Project is not in runtime profile" in capsys.readouterr().out


def test_source_mutation_during_probe_is_detected(monkeypatch, checkout):
    repo, path, workspace = checkout
    prepare_probe(monkeypatch, repo)

    def mutate(*args):
        (repo / "requirements.lock").write_text("packaging==99\n")
        return probe(repo)

    monkeypatch.setattr(runtime, "_probe", mutate)
    issues = runtime.check_runtime(path, workspace.root)["projects"][0]["issues"]
    assert "lock_mismatch" in issues and "dirty_checkout" in issues
