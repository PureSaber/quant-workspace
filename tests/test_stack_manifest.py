from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from quant_workspace.cli import main
from quant_workspace.loader import load_workspace
from quant_workspace.stack_manifest import (
    STACK_MANIFEST_SCHEMA_VERSION,
    DependencyNode,
    LockFile,
    SchemaDeclaration,
    StackManifest,
    StackManifestReleaseError,
    TagInfo,
    canonical_manifest_bytes,
    discover_stack,
    load_stack_manifest,
    normalize_origin,
    validate_stack_manifest,
    write_stack_manifest,
)

CREATED_AT = "2026-08-29T00:00:00Z"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _repo_pyproject(
    package: str,
    *,
    version: str = "1.0.0",
    dynamic_version: bool = False,
    dependencies: tuple[str, ...] = (),
    layer: str = "strategy",
    schemas: bool = True,
    lock_files: tuple[str, ...] = ("requirements.lock",),
) -> str:
    dependency_lines = ",\n".join(f"  {json.dumps(value)}" for value in dependencies)
    schema_line = 'schemas = [{ id = "standard/v2", version = "2.0.0" }]' if schemas else ""
    lock_line = f"lock-files = {json.dumps(list(lock_files))}" if lock_files else ""
    version_line = (
        'dynamic = ["version"]' if dynamic_version else f"version = {json.dumps(version)}"
    )
    setuptools_dynamic = (
        f'\n[tool.setuptools.packages.find]\nwhere = ["src"]\n'
        f'\n[tool.setuptools.dynamic]\nversion = {{ attr = "{package.replace("-", "_")}._version.__version__" }}\n'
        if dynamic_version
        else ""
    )
    return f"""[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = {json.dumps(package)}
{version_line}
requires-python = ">=3.10,<3.13"
dependencies = [
{dependency_lines}
]

[tool.quant-workspace]
layer = {json.dumps(layer)}
{schema_line}
{lock_line}
{setuptools_dynamic}
"""


def _create_repo(
    root: Path,
    project: str,
    *,
    package: str | None = None,
    version: str = "1.0.0",
    dynamic_version: bool = False,
    dependencies: tuple[str, ...] = (),
    layer: str = "strategy",
    schemas: bool = True,
    lock_files: tuple[str, ...] = ("requirements.lock",),
    tag: str | None = "v1.0.0",
    annotated: bool = False,
    origin: str | None = None,
) -> Path:
    repo = root / project
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Stack Test")
    _git(repo, "config", "user.email", "stack@example.invalid")
    _git(
        repo,
        "remote",
        "add",
        "origin",
        origin or f"git@github.com:PureSaber/{project}.git",
    )
    (repo / "pyproject.toml").write_text(
        _repo_pyproject(
            package or project,
            version=version,
            dynamic_version=dynamic_version,
            dependencies=dependencies,
            layer=layer,
            schemas=schemas,
            lock_files=lock_files,
        ),
        encoding="utf-8",
    )
    if dynamic_version:
        package_path = repo / "src" / (package or project).replace("-", "_")
        package_path.mkdir(parents=True)
        (package_path / "_version.py").write_text(
            f"__version__ = {json.dumps(version)}\n", encoding="utf-8"
        )
    for lock in lock_files:
        lock_path = (repo / lock).resolve()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(f"{project}=={version}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    if tag:
        args = ("tag", "-a", tag, "-m", f"release {tag}") if annotated else ("tag", tag)
        _git(repo, *args)
    return repo


def _workspace(root: Path, projects: dict[str, str] | None = None):
    projects = projects or {path.name: path.name for path in root.iterdir() if path.is_dir()}
    config = root / "workspace.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "root": str(root),
                "allowed_schemas": [{"id": "standard/v2", "version": "2.0.0"}],
                "projects": {name: {"repo": repo} for name, repo in projects.items()},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return load_workspace(config)


@pytest.fixture
def release_workspace(tmp_path: Path):
    root = tmp_path / "stack"
    root.mkdir()
    base = _create_repo(root, "base-lib", layer="data", annotated=True)
    app = _create_repo(
        root,
        "strategy-app",
        dependencies=("base-lib @ git+https://github.com/PureSaber/base-lib.git@v1.0.0",),
    )
    return _workspace(root), base, app


def _codes(manifest: StackManifest) -> set[str]:
    return {issue.code for issue in validate_stack_manifest(manifest).issues}


def _rehash(manifest: StackManifest) -> StackManifest:
    digest = hashlib.sha256(canonical_manifest_bytes(manifest, include_hash=False)).hexdigest()
    return replace(manifest, manifest_hash=digest)


def _audit_for_release_failure(workspace) -> tuple[StackManifest, set[str]]:
    audit = discover_stack(workspace, "audit", CREATED_AT)
    assert audit.release_ready is False
    return audit, _codes(audit)


def test_release_manifest_is_complete_and_deterministic(release_workspace) -> None:
    workspace, base, _ = release_workspace
    manifests = [discover_stack(workspace, "release", CREATED_AT) for _ in range(3)]
    assert [item.manifest_hash for item in manifests] == [manifests[0].manifest_hash] * 3
    assert [canonical_manifest_bytes(item) for item in manifests] == [
        canonical_manifest_bytes(manifests[0])
    ] * 3

    manifest = manifests[0]
    assert manifest.schema_version == STACK_MANIFEST_SCHEMA_VERSION
    assert manifest.release_ready is True
    assert validate_stack_manifest(manifest).to_dict() == {
        "valid": True,
        "release_ready": True,
        "issues": [],
    }
    records = {record.project: record for record in manifest.repositories}
    assert records["base-lib"].path == "base-lib"
    assert records["base-lib"].origin == "https://github.com/PureSaber/base-lib"
    assert records["base-lib"].commit == _git(base, "rev-parse", "HEAD").lower()
    assert records["base-lib"].tags == (
        TagInfo("v1.0.0", annotated=True, target_commit=records["base-lib"].commit),
    )
    assert records["strategy-app"].tags[0].annotated is False
    assert records["strategy-app"].package == "strategy-app"
    assert records["strategy-app"].requires_python == ">=3.10,<3.13"
    dependency = records["strategy-app"].internal_dependencies[0]
    assert dependency.ref_kind == "tag"
    assert dependency.resolved_commit == records["base-lib"].commit
    assert records["base-lib"].external_locks[0].path == "requirements.lock"
    assert len(records["base-lib"].external_locks[0].sha256) == 64
    assert manifest.dependency_dag == (
        DependencyNode("base-lib", ()),
        DependencyNode("strategy-app", ("base-lib",)),
    )


def test_full_commit_internal_reference_is_release_safe(tmp_path: Path) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    base = _create_repo(root, "base-lib", layer="data")
    commit = _git(base, "rev-parse", "HEAD")
    _create_repo(
        root,
        "strategy-app",
        dependencies=(f"base-lib @ git+ssh://git@github.com/PureSaber/base-lib.git@{commit}",),
    )
    manifest = discover_stack(_workspace(root), "release", CREATED_AT)
    dependency = manifest.repositories[1].internal_dependencies[0]
    assert dependency.ref_kind == "commit"
    assert dependency.ref == dependency.resolved_commit == commit


def test_dirty_and_untagged_are_audit_warnings_and_release_errors(release_workspace) -> None:
    workspace, base, app = release_workspace
    (base / "dirty.txt").write_text("dirty", encoding="utf-8")
    _git(app, "tag", "-d", "v1.0.0")
    audit, codes = _audit_for_release_failure(workspace)
    assert {"REPOSITORY_DIRTY", "REPOSITORY_UNTAGGED", "PACKAGE_TAG_MISMATCH"} <= codes
    assert all(issue.severity == "warning" for issue in validate_stack_manifest(audit).issues)
    with pytest.raises(StackManifestReleaseError) as caught:
        discover_stack(workspace, "release", CREATED_AT)
    assert {"REPOSITORY_DIRTY", "REPOSITORY_UNTAGGED"} <= {
        issue.code for issue in caught.value.result.errors
    }


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("main", "DEPENDENCY_REF_FLOATING"),
        ("missing-v9", "DEPENDENCY_REF_FLOATING"),
    ],
)
def test_floating_internal_reference_fails_closed(
    tmp_path: Path, reference: str, expected: str
) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    _create_repo(root, "base-lib", layer="data")
    _create_repo(
        root,
        "strategy-app",
        dependencies=(f"base-lib @ git+https://github.com/PureSaber/base-lib.git@{reference}",),
    )
    audit, codes = _audit_for_release_failure(_workspace(root))
    assert expected in codes
    if reference == "missing-v9":
        dependency = audit.repositories[1].internal_dependencies[0]
        assert dependency.resolved_commit == ""
    with pytest.raises(StackManifestReleaseError):
        discover_stack(_workspace(root), "release", CREATED_AT)


def test_plain_version_internal_dependency_is_floating(tmp_path: Path) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    _create_repo(root, "base-lib", layer="data")
    _create_repo(root, "strategy-app", dependencies=("base-lib>=1.0",))
    audit, codes = _audit_for_release_failure(_workspace(root))
    assert "DEPENDENCY_REF_FLOATING" in codes
    assert audit.repositories[1].internal_dependencies[0].ref == ">=1.0"


@pytest.mark.parametrize(
    "url",
    [
        "git+ssh://git@github.com/PureSaber/base-lib.git",
        "git+https://github.com/PureSaber/base-lib.git",
        "git@github.com:PureSaber/base-lib.git",
    ],
)
def test_vcs_url_without_ref_is_floating_not_username_ref(tmp_path: Path, url: str) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    _create_repo(root, "base-lib", layer="data")
    _create_repo(root, "strategy-app", dependencies=(f"base-lib @ {url}",))
    audit, codes = _audit_for_release_failure(_workspace(root))
    dependency = audit.repositories[1].internal_dependencies[0]
    assert dependency.origin == "https://github.com/PureSaber/base-lib"
    assert dependency.ref == ""
    assert dependency.ref_kind == "floating"
    assert "DEPENDENCY_REF_FLOATING" in codes


def test_dependency_commit_and_origin_mismatch_fail_closed(release_workspace) -> None:
    workspace, base, app = release_workspace
    (base / "next.txt").write_text("next", encoding="utf-8")
    _git(base, "add", ".")
    _git(base, "commit", "-m", "next")
    _git(base, "tag", "v1.0.1")
    app_pyproject = app / "pyproject.toml"
    app_pyproject.write_text(
        app_pyproject.read_text(encoding="utf-8").replace(
            "https://github.com/PureSaber/base-lib.git",
            "https://mirror.invalid/PureSaber/base-lib.git",
        ),
        encoding="utf-8",
    )
    _git(app, "add", ".")
    _git(app, "commit", "-m", "mirror")
    _git(app, "tag", "-f", "v1.0.0")
    _, codes = _audit_for_release_failure(workspace)
    assert "DEPENDENCY_COMMIT_MISMATCH" in codes
    assert "DEPENDENCY_ORIGIN_MISMATCH" in codes


def test_missing_repository_and_dependency_target_are_reported(tmp_path: Path) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    _create_repo(
        root,
        "strategy-app",
        dependencies=("base-lib @ git+https://github.com/PureSaber/base-lib.git@v1.0.0",),
    )
    workspace = _workspace(root, {"strategy-app": "strategy-app", "ghost": "ghost"})
    audit, codes = _audit_for_release_failure(workspace)
    assert {
        "REPOSITORY_MISSING",
        "DEPENDENCY_TARGET_MISSING",
        "DAG_TARGET_MISSING",
    } <= codes
    ghost = next(repo for repo in audit.repositories if repo.project == "ghost")
    assert ghost.exists is False
    with pytest.raises(StackManifestReleaseError):
        discover_stack(workspace, "release", CREATED_AT)


def test_dependency_cycle_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    _create_repo(
        root,
        "alpha",
        dependencies=("beta @ git+https://github.com/PureSaber/beta.git@v1.0.0",),
    )
    _create_repo(
        root,
        "beta",
        dependencies=("alpha @ git+https://github.com/PureSaber/alpha.git@v1.0.0",),
    )
    audit, codes = _audit_for_release_failure(_workspace(root))
    assert "DEPENDENCY_CYCLE" in codes
    assert {node.project for node in audit.dependency_dag} == {"alpha", "beta"}


def test_reverse_layer_dependency_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    _create_repo(root, "strategy-lib", layer="strategy")
    _create_repo(
        root,
        "foundation",
        layer="data",
        dependencies=("strategy-lib @ git+https://github.com/PureSaber/strategy-lib.git@v1.0.0",),
    )
    _, codes = _audit_for_release_failure(_workspace(root))
    assert "DEPENDENCY_DIRECTION_INVALID" in codes


def test_missing_layer_cannot_bypass_dependency_direction_gate(tmp_path: Path) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    strategy = _create_repo(root, "custom-strategy")
    pyproject = strategy / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace('layer = "strategy"\n', ""),
        encoding="utf-8",
    )
    _git(strategy, "add", ".")
    _git(strategy, "commit", "-m", "omit layer")
    _git(strategy, "tag", "-f", "v1.0.0")
    _create_repo(
        root,
        "quant-data-kit",
        layer="data",
        dependencies=(
            "custom-strategy @ git+https://github.com/PureSaber/custom-strategy.git@v1.0.0",
        ),
    )
    workspace = _workspace(root)
    audit, codes = _audit_for_release_failure(workspace)
    assert "LAYER_MISSING" in codes
    assert (
        next(record for record in audit.repositories if record.project == "custom-strategy").layer
        == ""
    )
    with pytest.raises(StackManifestReleaseError):
        discover_stack(workspace, "release", CREATED_AT)


def test_invalid_pep508_dependency_is_preserved_as_a_release_error(tmp_path: Path) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    _create_repo(root, "base-lib", layer="data")
    _create_repo(root, "strategy-app", dependencies=("base-lib @",))
    workspace = _workspace(root)
    audit, codes = _audit_for_release_failure(workspace)
    assert "DEPENDENCY_REQUIREMENT_INVALID" in codes
    strategy = next(record for record in audit.repositories if record.project == "strategy-app")
    assert strategy.internal_dependencies[0].ref == "base-lib @"
    assert strategy.internal_dependencies[0].package == ""
    with pytest.raises(StackManifestReleaseError):
        discover_stack(workspace, "release", CREATED_AT)


@pytest.mark.parametrize(
    ("schemas", "locks", "expected"),
    [
        (False, ("requirements.lock",), "SCHEMA_MISSING"),
        (True, (), "LOCK_MISSING"),
        (True, ("missing.lock",), "LOCK_HASH_INVALID"),
    ],
)
def test_schema_and_lock_are_release_gates(
    tmp_path: Path,
    schemas: bool,
    locks: tuple[str, ...],
    expected: str,
) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    repo = _create_repo(root, "only", schemas=schemas, lock_files=locks)
    if expected == "LOCK_HASH_INVALID":
        (repo / "missing.lock").unlink()
    _, codes = _audit_for_release_failure(_workspace(root))
    assert expected in codes


def test_disallowed_and_malformed_schema_are_reported(tmp_path: Path) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    repo = _create_repo(root, "only")
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            'schemas = [{ id = "standard/v2", version = "2.0.0" }]',
            'schemas = ["bad", { id = "unknown", version = "9" }]',
        ),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "bad schemas")
    _git(repo, "tag", "-f", "v1.0.0")
    _, codes = _audit_for_release_failure(_workspace(root))
    assert {"SCHEMA_INVALID", "SCHEMA_NOT_ALLOWED"} <= codes


def test_repository_and_lock_path_escape_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside_repo = _create_repo(tmp_path, "outside", lock_files=("../outside.lock",))
    assert outside_repo.parent == tmp_path
    workspace = _workspace(root, {"outside": "../outside"})
    _, codes = _audit_for_release_failure(workspace)
    assert {"REPOSITORY_PATH_ESCAPE", "LOCK_PATH_ESCAPE"} <= codes


def test_atomic_writer_refuses_overwrite_and_round_trips(release_workspace, tmp_path: Path) -> None:
    workspace, _, _ = release_workspace
    manifest = discover_stack(workspace, "release", CREATED_AT)
    destination = tmp_path / "published" / "stack.json"
    write_stack_manifest(destination, manifest)
    original = destination.read_bytes()
    loaded = load_stack_manifest(destination)
    assert loaded == manifest
    assert validate_stack_manifest(loaded).valid
    with pytest.raises(FileExistsError):
        write_stack_manifest(destination, manifest)
    assert destination.read_bytes() == original
    assert not list(destination.parent.glob("*.tmp"))


def test_writer_rejects_invalid_manifest_before_publish(release_workspace, tmp_path: Path) -> None:
    workspace, _, _ = release_workspace
    manifest = discover_stack(workspace, "release", CREATED_AT)
    destination = tmp_path / "invalid.json"
    with pytest.raises(ValueError, match="invalid stack manifest"):
        write_stack_manifest(destination, replace(manifest, manifest_hash="0" * 64))
    assert not destination.exists()


def test_writer_loses_no_clobber_race_and_cleans_temp(
    release_workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _, _ = release_workspace
    manifest = discover_stack(workspace, "release", CREATED_AT)
    destination = tmp_path / "race.json"

    def _racing_link(_source: Path, target: Path) -> None:
        Path(target).write_text("winner", encoding="utf-8")
        raise FileExistsError

    monkeypatch.setattr(os, "link", _racing_link)
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        write_stack_manifest(destination, manifest)
    assert destination.read_text(encoding="utf-8") == "winner"
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_stack_manifest_and_verify(release_workspace, tmp_path: Path, capsys) -> None:
    workspace, _, _ = release_workspace
    destination = tmp_path / "stack.json"
    assert (
        main(
            [
                "--config",
                str(workspace.config_path),
                "stack-manifest",
                "--mode",
                "release",
                "--created-at",
                CREATED_AT,
                "--out",
                str(destination),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["valid"] is True
    assert main(["verify-stack", str(destination)]) == 0
    assert json.loads(capsys.readouterr().out)["release_ready"] is True


def test_cli_reports_release_failure_and_invalid_file(
    release_workspace, tmp_path: Path, capsys
) -> None:
    workspace, base, _ = release_workspace
    (base / "dirty").write_text("x", encoding="utf-8")
    assert (
        main(
            [
                "--config",
                str(workspace.config_path),
                "stack-manifest",
                "--mode",
                "release",
                "--out",
                str(tmp_path / "never.json"),
            ]
        )
        == 2
    )
    assert "REPOSITORY_DIRTY" in capsys.readouterr().err
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json", encoding="utf-8")
    assert main(["verify-stack", str(invalid)]) == 2
    assert "Cannot read StackManifest" in capsys.readouterr().err

    existing = tmp_path / "existing.json"
    existing.write_text("keep", encoding="utf-8")
    assert (
        main(
            [
                "--config",
                str(workspace.config_path),
                "stack-manifest",
                "--mode",
                "audit",
                "--out",
                str(existing),
            ]
        )
        == 2
    )
    assert existing.read_text(encoding="utf-8") == "keep"
    assert "Refusing to overwrite" in capsys.readouterr().err


def test_tamper_and_noncanonical_fields_fail_validation(release_workspace) -> None:
    workspace, _, _ = release_workspace
    manifest = discover_stack(workspace, "release", CREATED_AT)
    assert "MANIFEST_HASH_MISMATCH" in _codes(replace(manifest, manifest_hash="0" * 64))
    assert "RELEASE_READY_MISMATCH" in _codes(_rehash(replace(manifest, release_ready=False)))
    assert "SCHEMA_VERSION_INVALID" in _codes(replace(manifest, schema_version="9"))
    assert "MODE_INVALID" in _codes(replace(manifest, mode="broken"))  # type: ignore[arg-type]
    assert "CREATED_AT_NOT_CANONICAL" in _codes(
        replace(manifest, created_at="2026-08-29T08:00:00+08:00")
    )
    assert "CREATED_AT_INVALID" in _codes(replace(manifest, created_at="not-a-time"))
    assert "CONFIG_HASH_INVALID" in _codes(replace(manifest, workspace_config_sha256="bad"))


def test_static_manifest_structural_failures(release_workspace) -> None:
    workspace, _, _ = release_workspace
    manifest = discover_stack(workspace, "release", CREATED_AT)
    base, app = manifest.repositories
    duplicate_package = replace(app, package=base.package)
    duplicate_project = replace(app, project=base.project)
    bad_origin = replace(base, origin="git@github.com:PureSaber/base-lib.git")
    bad_tag = replace(base, tags=(replace(base.tags[0], target_commit="0" * 40),))
    bad_layer = replace(base, layer="unknown")
    bad_lock = replace(base, external_locks=(LockFile("/escape", "0" * 64),))
    bad_schema = replace(base, schemas=(SchemaDeclaration("not-allowed", "1"),))

    cases = [
        (replace(manifest, repositories=(base, duplicate_project)), "DUPLICATE_PROJECT"),
        (replace(manifest, repositories=(base, duplicate_package)), "DUPLICATE_PACKAGE"),
        (replace(manifest, repositories=(bad_origin, app)), "ORIGIN_NOT_CANONICAL"),
        (replace(manifest, repositories=(bad_tag, app)), "TAG_COMMIT_MISMATCH"),
        (replace(manifest, repositories=(bad_layer, app)), "LAYER_INVALID"),
        (replace(manifest, repositories=(bad_lock, app)), "LOCK_PATH_ESCAPE"),
        (
            replace(manifest, repositories=(replace(base, path="..\\escape"), app)),
            "REPOSITORY_PATH_ESCAPE",
        ),
        (
            replace(manifest, repositories=(replace(base, path="C:/escape"), app)),
            "REPOSITORY_PATH_ESCAPE",
        ),
        (
            replace(
                manifest,
                repositories=(replace(base, external_locks=(LockFile("..\\lock", "0" * 64),)), app),
            ),
            "LOCK_PATH_ESCAPE",
        ),
        (replace(manifest, repositories=(bad_schema, app)), "SCHEMA_NOT_ALLOWED"),
        (
            replace(
                manifest,
                dependency_dag=(manifest.dependency_dag[0], manifest.dependency_dag[0]),
            ),
            "DUPLICATE_DAG_NODE",
        ),
        (replace(manifest, dependency_dag=manifest.dependency_dag[:1]), "DAG_PROJECT_MISMATCH"),
        (
            replace(
                manifest,
                dependency_dag=(DependencyNode("base-lib", ()), DependencyNode("strategy-app", ())),
            ),
            "DAG_EDGE_MISMATCH",
        ),
        (replace(manifest, repositories=(app, base)), "REPOSITORY_ORDER_INVALID"),
        (
            replace(manifest, dependency_dag=tuple(reversed(manifest.dependency_dag))),
            "DAG_ORDER_INVALID",
        ),
        (
            replace(
                manifest,
                allowed_schemas=(manifest.allowed_schemas[0], manifest.allowed_schemas[0]),
            ),
            "ALLOWED_SCHEMA_ORDER_INVALID",
        ),
        (replace(manifest, allowed_schemas=()), "ALLOWED_SCHEMA_MISSING"),
        (
            replace(manifest, repositories=(replace(base, tags=base.tags * 2), app)),
            "TAG_ORDER_INVALID",
        ),
        (
            replace(
                manifest,
                repositories=(
                    base,
                    replace(app, internal_dependencies=app.internal_dependencies * 2),
                ),
            ),
            "DEPENDENCY_ORDER_INVALID",
        ),
        (
            replace(manifest, repositories=(replace(base, schemas=base.schemas * 2), app)),
            "SCHEMA_ORDER_INVALID",
        ),
        (
            replace(
                manifest,
                repositories=(replace(base, external_locks=base.external_locks * 2), app),
            ),
            "LOCK_ORDER_INVALID",
        ),
        (
            replace(
                manifest,
                dependency_dag=(
                    manifest.dependency_dag[0],
                    DependencyNode("strategy-app", ("base-lib", "base-lib")),
                ),
            ),
            "DAG_DEPENDENCY_ORDER_INVALID",
        ),
    ]
    for candidate, expected in cases:
        candidate = replace(candidate, manifest_hash="0" * 64)
        assert expected in _codes(candidate)


def test_conflicting_and_inconsistent_dependency_records(release_workspace) -> None:
    workspace, _, _ = release_workspace
    manifest = discover_stack(workspace, "release", CREATED_AT)
    base, app = manifest.repositories
    dependency = app.internal_dependencies[0]
    conflict = replace(dependency, ref="v2.0.0")
    unresolved = replace(dependency, resolved_commit="")
    bad_commit_ref = replace(dependency, ref_kind="commit", ref="f" * 40)
    bad_tag_ref = replace(dependency, ref="not-head-tag")
    missing_target = replace(dependency, project="absent")
    bad_kind = replace(dependency, ref_kind="branch")  # type: ignore[arg-type]
    bad_package = replace(dependency, package="wrong-package")
    noncanonical_package = replace(dependency, package="Base_Lib")

    for changed, expected in [
        ((dependency, conflict), "DEPENDENCY_CONFLICT"),
        ((unresolved,), "DEPENDENCY_REF_UNRESOLVED"),
        ((bad_commit_ref,), "DEPENDENCY_COMMIT_REF_MISMATCH"),
        ((bad_tag_ref,), "DEPENDENCY_TAG_MISMATCH"),
        ((missing_target,), "DEPENDENCY_TARGET_MISSING"),
        ((bad_kind,), "DEPENDENCY_REF_KIND_INVALID"),
        ((bad_package,), "DEPENDENCY_PACKAGE_MISMATCH"),
        ((noncanonical_package,), "DEPENDENCY_PACKAGE_NOT_CANONICAL"),
    ]:
        changed_app = replace(app, internal_dependencies=changed)
        candidate = replace(manifest, repositories=(base, changed_app), manifest_hash="0" * 64)
        assert expected in _codes(candidate)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("git@github.com:PureSaber/repo.git", "https://github.com/PureSaber/repo"),
        ("ssh://git@github.com/PureSaber/repo.git", "https://github.com/PureSaber/repo"),
        ("git+http://GitHub.com/PureSaber/repo.git/", "https://github.com/PureSaber/repo"),
    ],
)
def test_origin_normalization(raw: str, expected: str) -> None:
    assert normalize_origin(raw) == expected


def test_model_deserialization_and_timestamp_validation(release_workspace, tmp_path: Path) -> None:
    workspace, _, _ = release_workspace
    manifest = discover_stack(workspace, "release", datetime(2026, 8, 29, tzinfo=timezone.utc))
    assert StackManifest.from_dict(manifest.to_dict()) == manifest
    with pytest.raises(ValueError, match="Invalid StackManifest"):
        StackManifest.from_dict({})
    bad_boolean = manifest.to_dict()
    bad_boolean["release_ready"] = "false"
    with pytest.raises(ValueError, match="must be a boolean"):
        StackManifest.from_dict(bad_boolean)
    bad_root = tmp_path / "array.json"
    bad_root.write_text("[]", encoding="utf-8")
    with pytest.raises(TypeError, match="root must be an object"):
        load_stack_manifest(bad_root)
    noncanonical = tmp_path / "pretty.json"
    noncanonical.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="not canonical"):
        load_stack_manifest(noncanonical)
    unknown = manifest.to_dict()
    unknown["unexpected"] = True
    unknown_path = tmp_path / "unknown.json"
    unknown_path.write_text(
        json.dumps(unknown, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown top-level"):
        load_stack_manifest(unknown_path)
    with pytest.raises(ValueError, match="timezone"):
        discover_stack(workspace, "audit", "2026-08-29T00:00:00")
    with pytest.raises(ValueError, match="ISO-8601"):
        discover_stack(workspace, "audit", "bad")
    with pytest.raises(ValueError, match="mode"):
        discover_stack(workspace, "invalid", CREATED_AT)  # type: ignore[arg-type]


def test_validation_result_views_and_issue_serialization(release_workspace) -> None:
    workspace, base, _ = release_workspace
    (base / "dirty").write_text("x", encoding="utf-8")
    audit = discover_stack(workspace, "audit", CREATED_AT)
    result = validate_stack_manifest(audit)
    assert result.valid
    assert result.errors == ()
    assert result.warnings
    assert result.warnings[0].to_dict()["project"]


def test_missing_origin_and_non_git_repository_are_auditable(tmp_path: Path) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    plain = root / "plain"
    plain.mkdir()
    (plain / "pyproject.toml").write_text(_repo_pyproject("plain"), encoding="utf-8")
    workspace = _workspace(root)
    audit, codes = _audit_for_release_failure(workspace)
    assert {"ORIGIN_MISSING", "COMMIT_INVALID", "REPOSITORY_UNTAGGED"} <= codes
    assert audit.repositories[0].exists


def test_package_version_python_range_and_tag_metadata_are_validated(release_workspace) -> None:
    workspace, _, _ = release_workspace
    manifest = discover_stack(workspace, "release", CREATED_AT)
    base, app = manifest.repositories
    cases = [
        (replace(base, version="not a version"), "PACKAGE_VERSION_INVALID"),
        (replace(base, requires_python="not a range"), "PYTHON_RANGE_INVALID"),
        (replace(base, tags=(replace(base.tags[0], name=""),)), "TAG_METADATA_INVALID"),
        (replace(base, exists=1), "REPOSITORY_STATE_INVALID"),
    ]
    for changed, expected in cases:
        candidate = replace(manifest, repositories=(changed, app), manifest_hash="0" * 64)
        assert expected in _codes(candidate)


def test_setuptools_dynamic_literal_version_is_read_without_importing_code(tmp_path: Path) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    repo = _create_repo(
        root,
        "dynamic-lib",
        version="1.2.3",
        dynamic_version=True,
        tag="v1.2.3",
        annotated=True,
    )
    version_file = repo / "src" / "dynamic_lib" / "_version.py"
    version_file.write_text(
        'raise RuntimeError("must not execute")\n__version__ = "1.2.3"\n', encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "prove static metadata parsing")
    _git(repo, "tag", "-fa", "v1.2.3", "-m", "release v1.2.3")

    manifest = discover_stack(_workspace(root), "release", CREATED_AT)
    assert manifest.repositories[0].version == "1.2.3"


def test_computed_setuptools_dynamic_version_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    repo = _create_repo(root, "dynamic-lib", dynamic_version=True)
    version_file = repo / "src" / "dynamic_lib" / "_version.py"
    version_file.write_text('__version__ = ".".join(("1", "0", "0"))\n', encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "computed version")
    _git(repo, "tag", "-f", "v1.0.0")

    _, codes = _audit_for_release_failure(_workspace(root))
    assert "PACKAGE_METADATA_MISSING" in codes


@pytest.mark.parametrize("allowed", [[], {"id": "standard/v2", "version": "2.0.0"}])
def test_explicit_empty_or_malformed_allowed_schema_fails_closed(
    release_workspace, allowed
) -> None:
    workspace, _, _ = release_workspace
    raw = yaml.safe_load(workspace.config_path.read_text(encoding="utf-8"))
    raw["allowed_schemas"] = allowed
    workspace.config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    audit, codes = _audit_for_release_failure(load_workspace(workspace.config_path))
    assert audit.release_ready is False
    assert {"ALLOWED_SCHEMA_MISSING", "ALLOWED_SCHEMA_INVALID"} & codes


def test_optional_internal_dependency_is_discovered(tmp_path: Path) -> None:
    root = tmp_path / "stack"
    root.mkdir()
    _create_repo(root, "base-lib", layer="data")
    app = _create_repo(root, "strategy-app")
    pyproject = app / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8")
        + '\n[project.optional-dependencies]\nworkspace = ["base-lib @ git+https://github.com/PureSaber/base-lib.git@v1.0.0"]\n',
        encoding="utf-8",
    )
    _git(app, "add", ".")
    _git(app, "commit", "-m", "optional")
    _git(app, "tag", "-f", "v1.0.0")
    manifest = discover_stack(_workspace(root), "release", CREATED_AT)
    assert manifest.repositories[1].internal_dependencies[0].package == "base-lib"
