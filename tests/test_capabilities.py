import copy
import json
import subprocess
from pathlib import Path

import pytest

from quant_workspace.capabilities import load_capabilities, source_inventory, validate_capabilities
from quant_workspace.cli import main
from quant_workspace.loader import load_workspace


def _catalog():
    catalog = copy.deepcopy(load_capabilities())
    project = next(p for p in catalog["projects"] if p["id"] == "quant-workspace")
    project["evidence"] = ["README.md"]
    catalog["projects"] = [project]
    catalog["relationships"] = []
    catalog["release_scopes"]["m8_runtime"]["projects"] = [project["id"]]
    return catalog


def _repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README.md").write_text("test source", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )


def test_development_and_release_scopes_match_their_own_configs():
    catalog = load_capabilities()
    root = Path(__file__).resolve().parents[1]
    development = load_workspace(root / "configs/platform.workspace.yaml")
    release = load_workspace(root / "configs/v2.release.workspace.yaml")
    assert {p["id"] for p in catalog["projects"]} == set(development.projects)
    assert set(catalog["release_scopes"]["m8_runtime"]["projects"]) == set(release.projects)
    assert len(development.projects) == 23
    assert len(release.projects) == 14
    assert "quant-studio" not in release.projects
    assert "quant-us-equity" in development.projects


def test_inventory_reports_missing_dirty_and_missing_evidence_without_certifying(tmp_path):
    catalog = _catalog()
    missing = source_inventory(catalog, tmp_path)
    assert not missing["source_inventory_complete"]
    assert missing["projects"][0]["source_status"] == "missing"
    repo = tmp_path / "quant-workspace"
    _repo(repo)
    present = source_inventory(catalog, tmp_path)
    assert present["source_inventory_complete"]
    assert not present["projects"][0]["dirty"]
    assert len(present["projects"][0]["revision"]) == 40
    assert not any(present["claims"].values())
    (repo / "untracked.txt").write_text("pending", encoding="utf-8")
    assert source_inventory(catalog, tmp_path)["projects"][0]["dirty"]
    (repo / "README.md").unlink()
    result = source_inventory(catalog, tmp_path)
    assert not result["source_inventory_complete"]
    assert result["projects"][0]["missing_evidence"] == ["README.md"]


def test_inventory_rejects_nested_directory_masquerading_as_checkout(tmp_path):
    parent = tmp_path / "parent"
    _repo(parent)
    (parent / "quant-workspace").mkdir()
    result = source_inventory(_catalog(), parent)
    assert result["projects"][0]["source_status"] == "unverifiable"
    assert not result["source_inventory_complete"]


@pytest.mark.parametrize("change", ["duplicate", "escape", "unknown-edge", "unknown-release"])
def test_invalid_catalog_cannot_drive_inventory(change):
    catalog = _catalog()
    if change == "duplicate":
        catalog["projects"] *= 2
    elif change == "escape":
        catalog["projects"][0]["evidence"] = ["../secret.txt"]
    elif change == "unknown-edge":
        catalog["relationships"] = [{"from": "absent", "to": "quant-workspace", "contract": "x"}]
    else:
        catalog["release_scopes"]["m8_runtime"]["projects"] = ["absent"]
    with pytest.raises(ValueError):
        validate_capabilities(catalog)


def test_capabilities_cli_is_available_without_a_workspace_config(capsys):
    assert main(["capabilities"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["projects"]) == 23


def test_inventory_cli_exits_nonzero_when_sources_are_missing(tmp_path, capsys):
    config = tmp_path / "workspace.yaml"
    config.write_text("root: .\nprojects: {}\n", encoding="utf-8")
    assert main(["--config", str(config), "capabilities", "--inventory"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert not result["source_inventory_complete"]
    assert not result["claims"]["runtime_verified"]
