from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest
from packaging.markers import Marker
from packaging.specifiers import SpecifierSet

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
    executable = runtime.python_path(repo / ".venv").resolve()
    return {
        "prefix": str(repo / ".venv"),
        "base_prefix": str(repo / "base"),
        "version": "3.12.14",
        "marker_environment": {"python_version": "3.12", "extra": ""},
        "include_system_site_packages": False,
        "interpreter": str(executable),
        "creation_executable": None,
        "implementation": "CPython",
        "implementation_name": "cpython",
        "implementation_version": "3.12.14",
        "metadata_source": "fixture",
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
    monkeypatch.setattr(runtime, "_read_environment", lambda *args: value or probe(repo))


def repin_lock(repo, path, workspace, text):
    (repo / "requirements.lock").write_text(text)
    git(repo, "add", "requirements.lock")
    git(repo, "commit", "-m", "update fixture lock")
    profile = runtime.create_profile(workspace, ["sample"], python=">=3.10,<4")
    path.write_text(json.dumps(profile))


def write_distribution(site, name, version, *, requires=(), requires_python=None, direct_url=None):
    metadata = site / f"{name.replace('-', '_')}-{version}.dist-info"
    metadata.mkdir()
    lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    if requires_python:
        lines.append(f"Requires-Python: {requires_python}")
    lines.extend(f"Requires-Dist: {requirement}" for requirement in requires)
    (metadata / "METADATA").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if direct_url is not None:
        (metadata / "direct_url.json").write_text(json.dumps(direct_url), encoding="utf-8")


def write_metadata_environment(repo, layout="windows", *, uv=False):
    environment = repo / f".{layout}-venv"
    home = repo / f"{layout}-base"
    home.mkdir()
    creator = repo / f"{layout}-creator"
    creator.mkdir()
    if layout == "windows":
        executable = environment / "Scripts" / "python.exe"
        creation_executable = creator / "python.exe"
        site = environment / "Lib" / "site-packages"
    else:
        executable = environment / "bin" / "python"
        creation_executable = creator / "python3.12"
        site = environment / "lib" / "python3.12" / "site-packages"
    executable.parent.mkdir(parents=True)
    executable.touch()
    creation_executable.touch()
    site.mkdir(parents=True)
    if uv:
        config = (
            f"home = {home}\nimplementation = CPython\nversion_info = 3.12\n"
            "include-system-site-packages = false\n"
        )
    else:
        config = (
            f"home = {home}\ninclude-system-site-packages = false\nversion = 3.12.14\n"
            f"executable = {creation_executable}\n"
        )
    (environment / "pyvenv.cfg").write_text(config, encoding="utf-8")
    write_distribution(site, "packaging", "26.3")
    write_distribution(
        site,
        "sample",
        "0.1",
        requires=["packaging>=23"],
        requires_python=">=3.12",
        direct_url={"url": repo.as_uri(), "dir_info": {"editable": True}},
    )
    return environment, executable, site


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
    assert report["claims"]["target_interpreter_executed"] is False
    assert report["claims"]["interpreter_binary_authenticated"] is False
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


def test_unlocked_distribution_blocks_but_bootstrap_pip_is_reported(monkeypatch, checkout):
    repo, path, workspace = checkout
    value = probe(repo)
    value["distributions"].append(
        {"name": "unlocked-extra", "version": "1", "requires": [], "direct_url": None}
    )
    prepare_probe(monkeypatch, repo, value)
    row = runtime.check_runtime(path, workspace.root)["projects"][0]
    assert "unlocked_distribution:unlocked-extra" in row["issues"]

    value["distributions"].pop()
    value["distributions"].append(
        {"name": "pip", "version": "25.0", "requires": [], "direct_url": None}
    )
    row = runtime.check_runtime(path, workspace.root)["projects"][0]
    assert row["status"] == "ready"
    assert row["bootstrap_tools"] == [
        {
            "name": "pip",
            "version": "25.0",
            "locked": False,
            "lock_applicability_verified": True,
            "content_authenticated": False,
        }
    ]
    value["distributions"][-1]["direct_url"] = {
        "url": "https://example.invalid/pip.whl",
        "archive_info": {},
    }
    row = runtime.check_runtime(path, workspace.root)["projects"][0]
    assert "unlocked_distribution:pip" in row["issues"]


def test_major_minor_metadata_blocks_patch_sensitive_constraints(monkeypatch, checkout):
    repo, path, workspace = checkout
    (repo / "requirements.lock").write_text("packaging==26.3 ; python_full_version >= '3.12.1'\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "patch-sensitive metadata")
    profile = runtime.create_profile(workspace, ["sample"], python=">=3.12.1,<3.13")
    path.write_text(json.dumps(profile))
    value = probe(repo)
    value["version"] = "3.12"
    value["marker_environment"]["python_full_version"] = "3.12"
    value["distributions"][0]["requires_python"] = ">=3.12.1"
    value["distributions"][1]["requires"] = ["packaging; python_full_version >= '3.12.1'"]
    prepare_probe(monkeypatch, repo, value)
    issues = set(runtime.check_runtime(path, workspace.root)["projects"][0]["issues"])
    assert {
        "metadata_insufficient:python_patch_version",
        "metadata_insufficient:lock_marker:packaging",
        "metadata_insufficient:requires_python:packaging",
        "metadata_insufficient:dependency_marker:sample:packaging",
    } <= issues


@pytest.mark.parametrize(
    "specifier",
    ["!=3.12.1", "!=3.12.1.*", ">3.12.0,<3.12.999999", "==3.12", "<=3.12.0"],
)
def test_major_minor_version_never_proves_patch_sensitive_set(specifier):
    assert runtime._version_matches("3.12", SpecifierSet(specifier)) is None
    assert runtime._version_matches("3.12", SpecifierSet(">=3.12,<3.13")) is True


def test_marker_evaluation_blocks_unavailable_implementation_metadata(checkout):
    repo, _, _ = checkout
    value = probe(repo)
    value["version"] = "3.12"
    value["implementation_version"] = None
    assert runtime._marker_applies(Marker("implementation_version >= '3.12.1'"), value) is None
    value["version"] = "3.12.14"
    value["implementation"] = None
    value["implementation_name"] = None
    assert runtime._marker_applies(Marker("implementation_name == 'cpython'"), value) is None


def test_dirty_and_wrong_revision_do_not_read_environment(monkeypatch, checkout):
    repo, path, workspace = checkout
    monkeypatch.setattr(
        runtime,
        "_read_environment",
        lambda *a: pytest.fail("untrusted checkout environment read"),
    )
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
    for text in [
        "foo>=1",
        "-e .",
        "-r other.txt",
        "--index-url https://example.invalid/simple",
        "foo @ https://example.invalid/pkg.whl",
    ]:
        with pytest.raises(ValueError):
            runtime.lock_requirements(text)


def test_lock_parser_accepts_only_tracked_repository_wheels(checkout):
    repo, _, _ = checkout
    wheels = repo / "vendor" / "wheels"
    wheels.mkdir(parents=True)
    (wheels / "akshare-1.18.88.post1-py3-none-any.whl").write_bytes(b"fixed fixture")
    git(repo, "add", "vendor/wheels")
    git(repo, "commit", "-m", "vendor fixed wheel")
    rows = runtime.lock_requirements(
        "--find-links vendor/wheels\nakshare==1.18.88.post1\n", repo=repo
    )
    assert [str(row) for row in rows] == ["akshare==1.18.88.post1"]

    (wheels / "untracked-1.0-py3-none-any.whl").write_bytes(b"ignored or untracked")
    with pytest.raises(ValueError, match="tracked by Git"):
        runtime.lock_requirements("--find-links vendor/wheels\nakshare==1.18.88.post1\n", repo=repo)


@pytest.mark.parametrize(
    "value", ["../wheels", "/vendor/wheels", "https://example.invalid/wheels", "vendor/../wheels"]
)
def test_find_links_rejects_paths_outside_canonical_repository_form(checkout, value):
    repo, _, _ = checkout
    with pytest.raises(ValueError):
        runtime.lock_requirements(f"--find-links {value}\nfoo==1\n", repo=repo)


def test_find_links_rejects_symlink_escape(checkout, tmp_path):
    repo, _, _ = checkout
    outside = tmp_path / "outside-wheels"
    outside.mkdir()
    (outside / "foo-1.0-py3-none-any.whl").write_bytes(b"outside")
    link = repo / "vendor" / "wheels"
    link.parent.mkdir()
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this host")
    with pytest.raises(ValueError, match="stay within the repository"):
        runtime.lock_requirements("--find-links vendor/wheels\nfoo==1\n", repo=repo)


@pytest.mark.parametrize("layout", ["windows", "posix"])
def test_metadata_reader_supports_windows_and_posix_without_executing_pth(tmp_path, layout):
    repo = tmp_path / "sample"
    repo.mkdir()
    environment, executable, site = write_metadata_environment(repo, layout)
    marker = tmp_path / "executed"
    (site / "malicious.pth").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    result = runtime._read_environment(environment, executable)
    assert result["version"] == "3.12.14"
    assert result["interpreter"] == str(executable.resolve())
    assert result["creation_executable"] is not None
    assert {d["name"] for d in result["distributions"]} == {"packaging", "sample"}
    assert (
        next(d for d in result["distributions"] if d["name"] == "sample")["requires_python"]
        == ">=3.12"
    )
    assert not marker.exists()


def test_metadata_reader_supports_uv_version_info_and_rejects_system_site_packages(tmp_path):
    repo = tmp_path / "sample"
    repo.mkdir()
    environment, executable, _ = write_metadata_environment(repo, "posix", uv=True)
    result = runtime._read_environment(environment, executable)
    assert result["version"] == "3.12"
    assert result["creation_executable"] is None
    config = environment / "pyvenv.cfg"
    config.write_text(config.read_text().replace("version_info = 3.12", "version_info = 3.12.14"))
    complete = runtime._read_environment(environment, executable)
    assert complete["implementation_version"] == "3.12.14"
    config.write_text(
        config.read_text().replace(
            "include-system-site-packages = false", "include-system-site-packages = true"
        )
    )
    with pytest.raises(ValueError, match="System site packages"):
        runtime._read_environment(environment, executable)


def test_metadata_reader_requires_interpreter_and_existing_creator(tmp_path):
    repo = tmp_path / "sample"
    repo.mkdir()
    environment, executable, _ = write_metadata_environment(repo)
    executable.unlink()
    with pytest.raises(ValueError, match="interpreter is missing"):
        runtime._read_environment(environment, executable)
    executable.touch()
    config = environment / "pyvenv.cfg"
    config.write_text(config.read_text().replace("executable = ", "executable = C:/wrong/"))
    with pytest.raises(ValueError, match="creation executable must be an existing absolute file"):
        runtime._read_environment(environment, executable)


def test_metadata_reader_rejects_partial_bootstrap_record(tmp_path):
    repo = tmp_path / "sample"
    repo.mkdir()
    environment, executable, _ = write_metadata_environment(repo)
    config = environment / "pyvenv.cfg"
    with config.open("a", encoding="utf-8") as stream:
        stream.write("quant-workspace-metadata-source = trusted-bootstrap-creator\n")
    with pytest.raises(ValueError, match="Bootstrap metadata is incomplete"):
        runtime._read_environment(environment, executable)


def test_pyvenv_preserves_optional_creation_executable(tmp_path):
    environment = tmp_path / "nested-venv"
    subprocess.run(
        [sys.executable, "-I", "-m", "venv", "--without-pip", str(environment)],
        check=True,
        capture_output=True,
        text=True,
    )
    config = environment / "pyvenv.cfg"
    before = config.read_bytes()
    identity = runtime._read_pyvenv(environment, runtime.python_path(environment))
    configured = identity["fields"].get("executable")
    expected = str(Path(configured).resolve()) if configured else None
    assert identity["creation_executable"] == expected
    if configured:
        assert identity["creation_executable"] == str(Path(sys.executable).resolve())
    assert identity["implementation_name"] is None
    unrecorded = runtime._read_environment(environment, runtime.python_path(environment))
    assert runtime._marker_applies(Marker('implementation_name != "pypy"'), unrecorded) is None
    assert config.read_bytes() == before

    runtime._record_bootstrap_metadata(environment)
    recorded = runtime._read_environment(environment, runtime.python_path(environment))
    assert recorded["metadata_source"] == "trusted-bootstrap-creator"
    assert recorded["implementation_name"] == sys.implementation.name
    assert recorded["implementation_version"] == runtime._current_implementation_version()
    assert runtime._marker_applies(Marker("implementation_version >= '0'"), recorded) is True
    assert runtime._marker_applies(Marker('implementation_name != "pypy"'), recorded) is True
    with config.open("a", encoding="utf-8") as stream:
        stream.write("implementation = PyPy\n")
    with pytest.raises(ValueError, match="conflicts with pyvenv.cfg"):
        runtime._read_environment(environment, runtime.python_path(environment))


def test_git_lock_commit_and_provider_must_match(monkeypatch, checkout):
    repo, path, workspace = checkout
    sha = "a" * 40
    requirement = (
        f"packaging==26.3\ndependency @ git+https://example.invalid/dependency.git@{sha}\n"
    )
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
                "vcs_info": {
                    "vcs": "git",
                    "requested_revision": sha,
                    "commit_id": sha,
                },
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
    value["distributions"][-1]["direct_url"] = {
        "url": "https://example.invalid/dependency.git",
        "vcs_info": {"vcs": "git", "commit_id": sha},
    }
    assert (
        "locked_git_mismatch:dependency"
        in runtime.check_runtime(path, workspace.root)["projects"][0]["issues"]
    )
    value["distributions"][-1]["direct_url"] = {
        "url": "https://example.invalid/dependency.git",
        "vcs_info": {
            "vcs": "hg",
            "requested_revision": sha,
            "commit_id": sha,
        },
    }
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


def test_locked_extra_activates_missing_dependency(monkeypatch, checkout):
    repo, path, workspace = checkout
    repin_lock(repo, path, workspace, "packaging==26.3\nfoo[bar]==1\nbad==1\n")
    value = probe(repo)
    value["distributions"].extend(
        [
            {
                "name": "foo",
                "version": "1",
                "requires": [
                    'missing; extra == "bar"',
                    'bad>=2; extra == "bar"',
                ],
                "direct_url": None,
            },
            {"name": "bad", "version": "1", "requires": [], "direct_url": None},
        ]
    )
    prepare_probe(monkeypatch, repo, value)
    issues = runtime.check_runtime(path, workspace.root)["projects"][0]["issues"]
    assert "missing_dependency:foo:missing" in issues
    assert "incompatible_dependency:foo:bad" in issues


def test_nested_extras_reach_fixed_point_through_cycle(monkeypatch, checkout):
    repo, path, workspace = checkout
    repin_lock(repo, path, workspace, "packaging==26.3\nfoo[root]==1\nmiddle==1\n")
    value = probe(repo)
    value["distributions"].extend(
        [
            {
                "name": "foo",
                "version": "1",
                "requires": [
                    'middle[nested]; extra == "root"',
                    'missing; extra == "loop"',
                ],
                "direct_url": None,
            },
            {
                "name": "middle",
                "version": "1",
                "requires": ['foo[loop]; extra == "nested"'],
                "direct_url": None,
            },
        ]
    )
    prepare_probe(monkeypatch, repo, value)
    issues = runtime.check_runtime(path, workspace.root)["projects"][0]["issues"]
    assert issues.count("missing_dependency:foo:missing") == 1


def test_unrequested_extra_dependency_is_inactive(monkeypatch, checkout):
    repo, path, workspace = checkout
    repin_lock(repo, path, workspace, "packaging==26.3\nfoo==1\n")
    value = probe(repo)
    value["distributions"].append(
        {
            "name": "foo",
            "version": "1",
            "requires": ['missing; extra == "bar"'],
            "direct_url": None,
        }
    )
    prepare_probe(monkeypatch, repo, value)
    issues = runtime.check_runtime(path, workspace.root)["projects"][0]["issues"]
    assert not any(issue.startswith("missing_dependency:foo:") for issue in issues)


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
    logged = json.loads((repo / ".venv/bootstrap-0.log").read_text())
    assert logged["status"] == "failed"
    assert logged["returncode"] == 1
    assert logged["stdout"] == "partial install"
    assert logged["stderr"] == "failure"


def test_bootstrap_success_verifies_selected_environment(monkeypatch, checkout):
    repo, path, workspace = checkout
    real_run = subprocess.run

    def invoke(command, **kwargs):
        if command[0] == "git":
            return real_run(command, **kwargs)
        target = runtime.python_path(repo / ".venv")
        target.parent.mkdir(exist_ok=True)
        target.touch()
        config = repo / ".venv" / "pyvenv.cfg"
        if not config.exists():
            config.write_text(
                f"home = {Path(sys.base_prefix)}\n"
                "include-system-site-packages = false\n"
                f"version = {platform.python_version()}\n"
                f"executable = {Path(sys.executable)}\n"
            )
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(runtime.subprocess, "run", invoke)
    monkeypatch.setattr(runtime, "_read_environment", lambda *a: probe(repo))
    assert runtime.bootstrap(path, workspace.root, "sample", execute=True)["executed"]
    assert len(list((repo / ".venv").glob("bootstrap-*.log"))) == 4
    assert all(
        json.loads(log.read_text())["status"] == "succeeded"
        for log in (repo / ".venv").glob("bootstrap-*.log")
    )
    identity = runtime._read_pyvenv(repo / ".venv", runtime.python_path(repo / ".venv"))
    assert identity["metadata_source"] == "trusted-bootstrap-creator"


def test_bootstrap_metadata_failure_retains_structured_log(monkeypatch, checkout):
    repo, path, workspace = checkout
    real_run = subprocess.run

    def invoke(command, **kwargs):
        if command[0] == "git":
            return real_run(command, **kwargs)
        target = runtime.python_path(repo / ".venv")
        target.parent.mkdir(exist_ok=True)
        target.touch()
        return subprocess.CompletedProcess(command, 0, "venv created", "")

    monkeypatch.setattr(runtime.subprocess, "run", invoke)
    with pytest.raises(ValueError, match="metadata recording failed"):
        runtime.bootstrap(path, workspace.root, "sample", execute=True)
    logged = json.loads((repo / ".venv/bootstrap-0.log").read_text())
    assert logged["status"] == "failed"
    assert logged["returncode"] == 0
    assert logged["stdout"] == "venv created"
    assert logged["error_type"] == "FileNotFoundError"


def test_bootstrap_timeout_retains_structured_partial_output(monkeypatch, checkout):
    repo, path, workspace = checkout
    real_run = subprocess.run

    def invoke(command, **kwargs):
        if command[0] == "git":
            return real_run(command, **kwargs)
        raise subprocess.TimeoutExpired(command, 900, output=b"partial", stderr=b"timed out")

    monkeypatch.setattr(runtime.subprocess, "run", invoke)
    with pytest.raises(ValueError, match="timed out; incomplete environment retained"):
        runtime.bootstrap(path, workspace.root, "sample", execute=True)
    logged = json.loads((repo / ".venv/bootstrap-0.log").read_text())
    assert logged["schema_version"] == "quant.runtime-bootstrap-step/v1"
    assert logged["status"] == "timed_out"
    assert logged["stdout"] == "partial"
    assert logged["stderr"] == "timed out"
    assert logged["error_type"] == "TimeoutExpired"


def test_bootstrap_interrupt_retains_structured_log(monkeypatch, checkout):
    repo, path, workspace = checkout
    real_run = subprocess.run

    def invoke(command, **kwargs):
        if command[0] == "git":
            return real_run(command, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(runtime.subprocess, "run", invoke)
    with pytest.raises(KeyboardInterrupt):
        runtime.bootstrap(path, workspace.root, "sample", execute=True)
    logged = json.loads((repo / ".venv/bootstrap-0.log").read_text())
    assert logged["status"] == "interrupted"
    assert logged["error_type"] == "KeyboardInterrupt"


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

    monkeypatch.setattr(runtime, "_read_environment", mutate)
    issues = runtime.check_runtime(path, workspace.root)["projects"][0]["issues"]
    assert "lock_mismatch" in issues and "dirty_checkout" in issues
