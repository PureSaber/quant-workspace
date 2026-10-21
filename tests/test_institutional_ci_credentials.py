import base64
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "token", ["test-only-private-read-secret", ""], ids=["configured", "empty"]
)
def test_private_read_secret_only_reaches_git_fetch(tmp_path, monkeypatch, token):
    source = Path(__file__).parents[1] / "profiles/institutional-research/ci.py"
    spec = importlib.util.spec_from_file_location("research_ci", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    here = tmp_path / "stack/quant-workspace/profiles/institutional-research"
    here.mkdir(parents=True)
    (here / "sources.lock.json").write_text(json.dumps({"quant-options": "a" * 40}))
    monkeypatch.setattr(module, "__file__", str(here / "ci.py"))
    monkeypatch.setenv("QUANT_RESEARCH_READ_TOKEN", token)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.longpaths")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    monkeypatch.delenv("GIT_CONFIG_KEY_1", raising=False)
    monkeypatch.delenv("GIT_CONFIG_VALUE_1", raising=False)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs.get("env", module.os.environ.copy())))

    monkeypatch.setattr(module.subprocess, "run", run)
    module.main()
    assert len(calls) == 6
    for argv, env in calls:
        assert env.get("QUANT_RESEARCH_READ_TOKEN") is None
        assert env.get("GIT_CONFIG_VALUE_0") == "true"
        if "fetch" in argv and token:
            assert env.get("GIT_CONFIG_COUNT") == "2"
            encoded = env["GIT_CONFIG_VALUE_1"].removeprefix("AUTHORIZATION: basic ")
            assert (
                base64.b64decode(encoded).decode() == "x-access-token:test-only-private-read-secret"
            )
        else:
            assert env.get("GIT_CONFIG_COUNT") == "1"
            assert env.get("GIT_CONFIG_VALUE_1") is None
            assert all("test-only-private-read-secret" not in v for v in env.values())
