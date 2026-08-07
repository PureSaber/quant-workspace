from pathlib import Path

import pytest
import yaml

from quant_workspace.loader import load_workspace, resolve_path


@pytest.fixture(autouse=True)
def _clear_workspace_root_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUANT_WORKSPACE_ROOT", raising=False)


def test_load_workspace_resolves_sibling_paths(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    (root / "alpha").mkdir(parents=True)
    (root / "beta" / "outputs").mkdir(parents=True)

    cfg = tmp_path / "workspace.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "root": str(root),
                "projects": {
                    "alpha": {"repo": "alpha", "state": "state"},
                    "beta": {"repo": "beta", "outputs": "outputs"},
                },
            }
        ),
        encoding="utf-8",
    )

    ws = load_workspace(cfg)
    assert ws.path("alpha", "repo") == (root / "alpha").resolve()
    assert ws.path("alpha", "state") == (root / "alpha" / "state").resolve()
    assert ws.path("beta", "outputs") == (root / "beta" / "outputs").resolve()


def test_lab_workspace_yaml(tmp_path: Path) -> None:
    root = tmp_path
    (root / "p1" / "outputs").mkdir(parents=True)
    cfg = root / "ws.yaml"
    cfg.write_text(
        yaml.safe_dump({"root": str(root), "projects": {"p1": {"repo": "p1", "outputs": "outputs"}}}),
        encoding="utf-8",
    )
    ws = load_workspace(cfg)
    lab = ws.lab_workspace_yaml()
    assert lab["projects"][0]["name"] == "p1"
    assert lab["projects"][0]["outputs"].endswith("outputs")


def test_resolve_path_helper(tmp_path: Path) -> None:
    root = tmp_path
    (root / "x").mkdir()
    cfg = root / "ws.yaml"
    cfg.write_text(yaml.safe_dump({"root": str(root), "projects": {"x": {"repo": "x"}}}), encoding="utf-8")
    assert resolve_path(cfg, "x", "repo") == (root / "x").resolve()
