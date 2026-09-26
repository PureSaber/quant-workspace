import importlib.util
import subprocess
from pathlib import Path

import pytest
import yaml

PROFILE = Path(__file__).resolve().parents[1] / "profiles/research-workbench"


def test_checkout_gate_rejects_dirty_or_different_repository(tmp_path):
    spec = importlib.util.spec_from_file_location("research_bootstrap", PROFILE / "bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    revision = module.git(tmp_path, "rev-parse", "HEAD")
    module.check_checkout(tmp_path, revision)
    with pytest.raises(ValueError, match="must be at"):
        module.check_checkout(tmp_path, "0" * 40)
    (tmp_path / "untracked").write_text("change")
    with pytest.raises(ValueError, match="uncommitted"):
        module.check_checkout(tmp_path, revision)


def test_templates_preserve_explicit_scope_and_do_not_reuse_holdout():
    for path in (PROFILE / "templates").glob("*.yaml"):
        recipe = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert recipe["mode"] == "exploratory"
        assert "holdout" not in recipe
        assert recipe["source"]["scope"]
        assert recipe["factors"]
        assert recipe["costs"]["participation_rate"] > 0
        assert recipe["interval"]["start"] < recipe["interval"]["end"]
