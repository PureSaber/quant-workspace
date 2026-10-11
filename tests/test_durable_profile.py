import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

PROFILE = Path(__file__).resolve().parents[1] / "profiles/durable-research"
SPEC = importlib.util.spec_from_file_location("durable_bootstrap", PROFILE / "bootstrap.py")
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def test_existing_installation_is_never_overwritten(tmp_path):
    marker = tmp_path / "original"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(SystemExit):
        bootstrap.main(["--root", str(tmp_path)])
    assert marker.read_text(encoding="utf-8") == "keep"
    assert list(tmp_path.iterdir()) == [marker]


def test_tampered_lock_is_rejected_before_creating_root(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    stack = json.loads((PROFILE / "stack.json").read_text(encoding="utf-8"))
    (profile / "stack.json").write_text(json.dumps(stack), encoding="utf-8")
    (profile / "requirements.lock").write_text("tampered", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "PROFILE", profile)
    monkeypatch.setattr(bootstrap.subprocess, "check_output", lambda *_a, **_k: "3.12")
    root = tmp_path / "installation"
    with pytest.raises(ValueError, match="lock identity"):
        bootstrap.main(["--root", str(root)])
    assert not root.exists()


@pytest.mark.parametrize(
    ("inside", "commit", "editable", "accepted"),
    [
        (True, "a" * 40, False, True),
        (False, "a" * 40, False, False),
        (True, "b" * 40, False, False),
        (True, "a" * 40, True, False),
    ],
)
def test_installed_identity_rejects_overlays_and_wrong_source(
    tmp_path, monkeypatch, inside, commit, editable, accepted
):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "stack.json").write_text(
        json.dumps({"repositories": {"quant-test": "a" * 40}}), encoding="utf-8"
    )
    prefix = tmp_path / "env"
    path = prefix / "site-packages/module.py" if inside else tmp_path / "source/module.py"
    source = json.dumps({"vcs_info": {"commit_id": commit}, "dir_info": {"editable": editable}})
    monkeypatch.setattr(bootstrap, "PROFILE", profile)
    monkeypatch.setattr(bootstrap, "MODULES", {"quant-test": "test.module"})
    monkeypatch.setattr(bootstrap.sys, "prefix", str(prefix))
    monkeypatch.setattr(
        bootstrap.importlib, "import_module", lambda _: SimpleNamespace(__file__=path)
    )
    monkeypatch.setattr(
        bootstrap.importlib.metadata,
        "distribution",
        lambda _: SimpleNamespace(read_text=lambda _: source),
    )
    if accepted:
        assert bootstrap.installed_identity()["quant-test"]["commit"] == commit
    else:
        with pytest.raises(ValueError):
            bootstrap.installed_identity()
