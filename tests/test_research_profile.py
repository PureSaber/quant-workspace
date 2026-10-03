import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PROFILE = Path(__file__).resolve().parents[1] / "profiles/research-workbench"


def test_bootstrap_selects_base_runtime_and_refuses_environment_replacement(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "research_runtime_bootstrap", PROFILE / "bootstrap.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    requested = str(tmp_path / "requested-python")
    environment = tmp_path / "environment"
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    old = {"base": "old-runtime", "version": [3, 12, 5]}
    selected = {"base": "selected-runtime", "version": [3, 12, 13]}
    commands = []

    def identity(argv, **kwargs):
        return json.dumps(selected if argv[0] == requested else old)

    monkeypatch.setattr(module.subprocess, "check_output", identity)
    monkeypatch.setattr(module.subprocess, "run", lambda argv, **kwargs: commands.append(argv))
    assert module.create_environment(environment, requested) == python
    assert commands == [[requested, "-m", "venv", str(environment)]]
    python.parent.mkdir(parents=True)
    python.write_bytes(b"existing interpreter")
    commands.clear()
    with pytest.raises(ValueError, match="new --env"):
        module.create_environment(environment, requested)
    assert commands == []
    assert python.read_bytes() == b"existing interpreter"
    monkeypatch.setattr(
        module.subprocess, "check_output", lambda *args, **kwargs: json.dumps(selected)
    )
    assert module.create_environment(environment, requested) == python
    assert commands == []


@pytest.mark.parametrize("version", ["3.10.0", "3.10.21", "3.11.0", "3.11.16", "3.12.5"])
@pytest.mark.parametrize("platform,os_name", [("win32", "nt"), ("linux", "posix")])
def test_research_lock_covers_declared_inputs_and_matches_manifest(version, platform, os_name):
    environment = {
        **default_environment(),
        "python_full_version": version,
        "python_version": ".".join(version.split(".")[:2]),
        "sys_platform": platform,
        "os_name": os_name,
    }
    lock = PROFILE / "requirements.lock"
    stack = json.loads((PROFILE / "stack.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(lock.read_bytes()).hexdigest() == stack["requirements_sha256"]
    selected = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if not requirement.marker or requirement.marker.evaluate(environment):
            key = canonicalize_name(requirement.name)
            assert key not in selected, f"duplicate active pin: {key}"
            selected[key] = requirement
    for line in (PROFILE / "requirements.in").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate(environment):
            continue
        pin = selected[canonicalize_name(requirement.name)]
        if requirement.url:
            assert pin.url == requirement.url
        else:
            versions = [item.version for item in pin.specifier if item.operator == "=="]
            assert len(versions) == 1
            assert requirement.specifier.contains(versions[0]), requirement.name


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


def test_dataset_root_belongs_to_dataset_command(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PROFILE))
    spec = importlib.util.spec_from_file_location("research_entrypoint", PROFILE / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    verified = []
    invoked = []
    monkeypatch.setattr(module, "verify", lambda root: verified.append(root) or [root / "src"])
    monkeypatch.setattr(module.subprocess, "call", lambda argv, **kw: invoked.append(argv) or 0)
    dataset = str(tmp_path / "dataset")
    assert module.main(["--root", str(tmp_path), "dataset", "inspect", "--root", dataset]) == 0
    assert verified == [tmp_path.resolve()]
    assert invoked[0][2:] == ["quant_data_kit.research_dataset", "inspect", "--root", dataset]
