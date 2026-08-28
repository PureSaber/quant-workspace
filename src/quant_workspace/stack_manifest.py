from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import yaml
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

try:  # pragma: no cover - Python 3.10 exercises the fallback in CI.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

from quant_workspace.models import Workspace

STACK_MANIFEST_SCHEMA_VERSION = "1.0.0"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STACK_MANIFEST_FIELDS = {
    "schema_version",
    "mode",
    "created_at",
    "workspace_config_sha256",
    "repositories",
    "dependency_dag",
    "allowed_schemas",
    "release_ready",
    "manifest_hash",
}
_LAYERS = {
    "data": 0,
    "contract": 0,
    "execution": 1,
    "strategy": 2,
    "portfolio-risk": 3,
    "reporting": 4,
    "orchestration": 5,
}
_DEFAULT_LAYERS = {
    "quant-data-kit": "data",
    "quant-lab": "contract",
    "quant-execution": "execution",
    "quant-paper-sim": "strategy",
    "quant-factors": "strategy",
    "a-share-multifactor": "strategy",
    "quant-futures-spread": "strategy",
    "quant-crypto-basis": "strategy",
    "quant-portfolio": "portfolio-risk",
    "quant-risk-monitor": "portfolio-risk",
    "quant-report-hub": "reporting",
    "quant-pipeline": "orchestration",
    "quant-workspace": "orchestration",
    "quant-agent": "orchestration",
}


@dataclass(frozen=True, order=True)
class TagInfo:
    name: str
    annotated: bool
    target_commit: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "annotated": self.annotated,
            "target_commit": self.target_commit,
        }


@dataclass(frozen=True, order=True)
class SchemaDeclaration:
    schema_id: str
    version: str

    def to_dict(self) -> dict[str, str]:
        return {"schema_id": self.schema_id, "version": self.version}


@dataclass(frozen=True, order=True)
class LockFile:
    path: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True, order=True)
class InternalDependency:
    package: str
    project: str
    ref: str
    ref_kind: Literal["tag", "commit", "floating"]
    resolved_commit: str
    origin: str

    def to_dict(self) -> dict[str, str]:
        return {
            "package": self.package,
            "project": self.project,
            "ref": self.ref,
            "ref_kind": self.ref_kind,
            "resolved_commit": self.resolved_commit,
            "origin": self.origin,
        }


@dataclass(frozen=True)
class RepositoryRecord:
    project: str
    path: str
    exists: bool
    origin: str
    commit: str
    branch: str
    dirty: bool
    tags: tuple[TagInfo, ...]
    package: str
    version: str
    requires_python: str
    layer: str
    internal_dependencies: tuple[InternalDependency, ...]
    schemas: tuple[SchemaDeclaration, ...]
    external_locks: tuple[LockFile, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "path": self.path,
            "exists": self.exists,
            "origin": self.origin,
            "commit": self.commit,
            "branch": self.branch,
            "dirty": self.dirty,
            "tags": [tag.to_dict() for tag in self.tags],
            "package": self.package,
            "version": self.version,
            "requires_python": self.requires_python,
            "layer": self.layer,
            "internal_dependencies": [dep.to_dict() for dep in self.internal_dependencies],
            "schemas": [schema.to_dict() for schema in self.schemas],
            "external_locks": [lock.to_dict() for lock in self.external_locks],
        }


@dataclass(frozen=True, order=True)
class DependencyNode:
    project: str
    depends_on: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"project": self.project, "depends_on": list(self.depends_on)}


@dataclass(frozen=True)
class StackManifest:
    schema_version: str
    mode: Literal["audit", "release"]
    created_at: str
    workspace_config_sha256: str
    repositories: tuple[RepositoryRecord, ...]
    dependency_dag: tuple[DependencyNode, ...]
    allowed_schemas: tuple[SchemaDeclaration, ...]
    release_ready: bool
    manifest_hash: str

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "created_at": self.created_at,
            "workspace_config_sha256": self.workspace_config_sha256,
            "repositories": [repo.to_dict() for repo in self.repositories],
            "dependency_dag": [node.to_dict() for node in self.dependency_dag],
            "allowed_schemas": [schema.to_dict() for schema in self.allowed_schemas],
            "release_ready": self.release_ready,
        }
        if include_hash:
            payload["manifest_hash"] = self.manifest_hash
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> StackManifest:
        try:
            unknown = sorted(set(payload) - _STACK_MANIFEST_FIELDS)
            if unknown:
                raise ValueError(f"unknown top-level fields: {unknown}")
            repositories = tuple(_repository_from_dict(item) for item in payload["repositories"])
            dag = tuple(
                DependencyNode(
                    project=str(item["project"]),
                    depends_on=tuple(str(value) for value in item["depends_on"]),
                )
                for item in payload["dependency_dag"]
            )
            allowed = tuple(_schema_from_dict(item) for item in payload["allowed_schemas"])
            return cls(
                schema_version=str(payload["schema_version"]),
                mode=str(payload["mode"]),  # type: ignore[arg-type]
                created_at=str(payload["created_at"]),
                workspace_config_sha256=str(payload["workspace_config_sha256"]),
                repositories=repositories,
                dependency_dag=dag,
                allowed_schemas=allowed,
                release_ready=_required_bool(payload["release_ready"], "release_ready"),
                manifest_hash=str(payload["manifest_hash"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid StackManifest payload: {exc}") from exc


@dataclass(frozen=True, order=True)
class ValidationIssue:
    severity: Literal["error", "warning"]
    code: str
    message: str
    project: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = {"severity": self.severity, "code": self.code, "message": self.message}
        if self.project:
            payload["project"] = self.project
        return payload


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    release_ready: bool
    issues: tuple[ValidationIssue, ...]

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "release_ready": self.release_ready,
            "issues": [issue.to_dict() for issue in self.issues],
        }


class StackManifestReleaseError(ValueError):
    def __init__(self, manifest: StackManifest, result: ValidationResult):
        self.manifest = manifest
        self.result = result
        codes = ", ".join(issue.code for issue in result.errors)
        super().__init__(f"Stack manifest is not release-ready: {codes}")


def _required_bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{field} must be a boolean")
    return value


def _repository_from_dict(payload: dict[str, Any]) -> RepositoryRecord:
    return RepositoryRecord(
        project=str(payload["project"]),
        path=str(payload["path"]),
        exists=_required_bool(payload["exists"], "repository.exists"),
        origin=str(payload["origin"]),
        commit=str(payload["commit"]),
        branch=str(payload["branch"]),
        dirty=_required_bool(payload["dirty"], "repository.dirty"),
        tags=tuple(
            TagInfo(
                name=str(item["name"]),
                annotated=_required_bool(item["annotated"], "tag.annotated"),
                target_commit=str(item["target_commit"]),
            )
            for item in payload["tags"]
        ),
        package=str(payload["package"]),
        version=str(payload["version"]),
        requires_python=str(payload["requires_python"]),
        layer=str(payload.get("layer", "")),
        internal_dependencies=tuple(
            InternalDependency(
                package=str(item["package"]),
                project=str(item["project"]),
                ref=str(item["ref"]),
                ref_kind=str(item["ref_kind"]),  # type: ignore[arg-type]
                resolved_commit=str(item["resolved_commit"]),
                origin=str(item["origin"]),
            )
            for item in payload["internal_dependencies"]
        ),
        schemas=tuple(_schema_from_dict(item) for item in payload["schemas"]),
        external_locks=tuple(
            LockFile(path=str(item["path"]), sha256=str(item["sha256"]))
            for item in payload["external_locks"]
        ),
    )


def _schema_from_dict(payload: dict[str, Any]) -> SchemaDeclaration:
    return SchemaDeclaration(schema_id=str(payload["schema_id"]), version=str(payload["version"]))


def canonical_manifest_bytes(manifest: StackManifest, *, include_hash: bool = True) -> bytes:
    return json.dumps(
        manifest.to_dict(include_hash=include_hash),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _manifest_hash(manifest: StackManifest) -> str:
    return hashlib.sha256(canonical_manifest_bytes(manifest, include_hash=False)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_origin(origin: str) -> str:
    value = origin.strip().removeprefix("git+")
    scp_match = re.fullmatch(r"git@([^:]+):(.+)", value)
    if scp_match:
        value = f"https://{scp_match.group(1)}/{scp_match.group(2)}"
    parsed = urlsplit(value)
    if parsed.scheme == "ssh" and parsed.hostname:
        path = parsed.path
        value = urlunsplit(("https", parsed.hostname.lower(), path, "", ""))
        parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        host = parsed.hostname.lower()
        port = f":{parsed.port}" if parsed.port else ""
        path = parsed.path.rstrip("/").removesuffix(".git")
        return urlunsplit(("https", f"{host}{port}", path, "", ""))
    if value.startswith("file://"):
        return Path(value[7:]).resolve().as_posix()
    path_value = Path(value)
    if path_value.is_absolute():
        return path_value.resolve().as_posix()
    return value.rstrip("/").removesuffix(".git")


def _git(repo: Path, *args: str) -> tuple[int, str]:
    process = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return process.returncode, process.stdout.strip()


def _git_value(repo: Path, *args: str) -> str:
    code, output = _git(repo, *args)
    return output if code == 0 else ""


def _tags_at_head(repo: Path, commit: str) -> tuple[TagInfo, ...]:
    names = _git_value(repo, "tag", "--points-at", commit).splitlines()
    tags: list[TagInfo] = []
    for name in sorted(value.strip() for value in names if value.strip()):
        object_type = _git_value(repo, "cat-file", "-t", f"refs/tags/{name}")
        target = _git_value(repo, "rev-parse", f"refs/tags/{name}^{{commit}}").lower()
        tags.append(TagInfo(name=name, annotated=object_type == "tag", target_commit=target))
    return tuple(tags)


def _normalize_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_schema_declarations(raw: Any) -> tuple[SchemaDeclaration, ...]:
    if not isinstance(raw, list):
        return ()
    declarations: set[SchemaDeclaration] = set()
    for item in raw:
        if isinstance(item, dict):
            declarations.add(
                SchemaDeclaration(
                    schema_id=str(item.get("id", item.get("schema_id", ""))),
                    version=str(item.get("version", "")),
                )
            )
        elif isinstance(item, str) and "@" in item:
            schema_id, version = item.rsplit("@", 1)
            declarations.add(SchemaDeclaration(schema_id=schema_id, version=version))
        else:
            declarations.add(SchemaDeclaration(schema_id="", version=""))
    return tuple(sorted(declarations))


def _read_project_metadata(repo: Path) -> dict[str, Any]:
    pyproject = repo / "pyproject.toml"
    if not pyproject.is_file():
        return {
            "package": "",
            "version": "",
            "requires_python": "",
            "requirements": (),
            "schemas": (),
            "lock_paths": (),
            "layer": "",
        }
    try:
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {
            "package": "",
            "version": "",
            "requires_python": "",
            "requirements": (),
            "schemas": (),
            "lock_paths": (),
            "layer": "",
        }
    project = payload.get("project") if isinstance(payload.get("project"), dict) else {}
    tool = payload.get("tool") if isinstance(payload.get("tool"), dict) else {}
    stack = tool.get("quant-workspace") if isinstance(tool.get("quant-workspace"), dict) else {}
    requirements: list[str] = []
    dependencies = project.get("dependencies", [])
    if isinstance(dependencies, list):
        requirements.extend(str(value) for value in dependencies)
    optional = project.get("optional-dependencies", {})
    if isinstance(optional, dict):
        for group in sorted(optional):
            values = optional[group]
            if isinstance(values, list):
                requirements.extend(str(value) for value in values)
    locks = stack.get("lock-files", stack.get("locks", []))
    lock_paths = tuple(sorted(str(value) for value in locks)) if isinstance(locks, list) else ()
    return {
        "package": str(project.get("name", "")),
        "version": str(project.get("version", "")),
        "requires_python": str(project.get("requires-python", "")),
        "requirements": tuple(requirements),
        "schemas": _parse_schema_declarations(stack.get("schemas", [])),
        "lock_paths": lock_paths,
        "layer": str(stack.get("layer", "")),
    }


def _relative_path(path: Path, root: Path) -> str:
    try:
        return Path(os.path.relpath(path.resolve(), root.resolve())).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _discover_locks(repo: Path, lock_paths: tuple[str, ...]) -> tuple[LockFile, ...]:
    locks: list[LockFile] = []
    for raw in lock_paths:
        candidate = Path(raw)
        resolved = candidate.resolve() if candidate.is_absolute() else (repo / candidate).resolve()
        relative = _relative_path(resolved, repo)
        digest = (
            "" if _path_escapes(relative) else _sha256_file(resolved) if resolved.is_file() else ""
        )
        locks.append(LockFile(path=relative, sha256=digest))
    return tuple(sorted(set(locks)))


def _split_vcs_reference(url: str) -> tuple[str, str]:
    value = url.split("#", 1)[0]
    raw = value.removeprefix("git+")
    parsed = urlsplit(raw)
    if parsed.scheme and parsed.netloc:
        marker = parsed.path.rfind("@")
        if marker < 0:
            return normalize_origin(raw), ""
        origin = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path[:marker], parsed.query, parsed.fragment)
        )
        return normalize_origin(origin), parsed.path[marker + 1 :]
    scp_match = re.fullmatch(r"(?P<prefix>git@[^:]+:)(?P<path>.+)", raw)
    if scp_match:
        path = scp_match.group("path")
        marker = path.rfind("@")
        if marker < 0:
            return normalize_origin(raw), ""
        return normalize_origin(f"{scp_match.group('prefix')}{path[:marker]}"), path[marker + 1 :]
    marker = raw.rfind("@")
    if marker < 0:
        return normalize_origin(raw), ""
    return normalize_origin(raw[:marker]), raw[marker + 1 :]


def _project_from_origin(origin: str) -> str:
    parsed = urlsplit(origin)
    if parsed.hostname and parsed.hostname.lower() == "github.com":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 2 and parts[0].lower() == "puresaber":
            return parts[1]
    return ""


def _resolve_dependency(
    requirement_text: str,
    package_projects: dict[str, str],
    origin_projects: dict[str, str],
    repos: dict[str, Path],
) -> InternalDependency | None:
    try:
        requirement = Requirement(requirement_text)
    except InvalidRequirement:
        return InternalDependency(
            package="",
            project="",
            ref=requirement_text,
            ref_kind="floating",
            resolved_commit="",
            origin="",
        )
    package = _normalize_package(requirement.name)
    dependency_origin = ""
    ref = ""
    if requirement.url:
        dependency_origin, ref = _split_vcs_reference(requirement.url)
    project = package_projects.get(package, "")
    if not project and dependency_origin:
        project = origin_projects.get(dependency_origin, "") or _project_from_origin(
            dependency_origin
        )
    if not project:
        return None
    if not requirement.url:
        return InternalDependency(
            package=package,
            project=project,
            ref=str(requirement.specifier),
            ref_kind="floating",
            resolved_commit="",
            origin="",
        )
    target = repos.get(project)
    resolved = ""
    ref_kind: Literal["tag", "commit", "floating"] = "floating"
    if _SHA40.fullmatch(ref.lower()):
        ref = ref.lower()
        ref_kind = "commit"
    elif target and _git(target, "show-ref", "--verify", "--quiet", f"refs/tags/{ref}")[0] == 0:
        ref_kind = "tag"
    if target and ref:
        resolved = _git_value(target, "rev-parse", f"{ref}^{{commit}}").lower()
    return InternalDependency(
        package=package,
        project=project,
        ref=ref,
        ref_kind=ref_kind,
        resolved_commit=resolved,
        origin=dependency_origin,
    )


def _normalize_created_at(created_at: str | datetime | None) -> str:
    if created_at is None:
        value = datetime.now(timezone.utc)
    elif isinstance(created_at, datetime):
        value = created_at
    else:
        text = created_at[:-1] + "+00:00" if created_at.endswith("Z") else created_at
        try:
            value = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("created_at must be an ISO-8601 timestamp") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_at must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _configured_allowed_schemas(
    workspace: Workspace,
) -> tuple[SchemaDeclaration, ...] | None:
    try:
        raw = yaml.safe_load(workspace.config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ()
    if not isinstance(raw, dict):
        return (SchemaDeclaration("", ""),)
    if "allowed_schemas" not in raw:
        return None
    declared = raw["allowed_schemas"]
    if not isinstance(declared, list):
        return (SchemaDeclaration("", ""),)
    return _parse_schema_declarations(declared)


def discover_stack(
    workspace: Workspace,
    mode: Literal["audit", "release"],
    created_at: str | datetime | None = None,
) -> StackManifest:
    if mode not in {"audit", "release"}:
        raise ValueError("mode must be 'audit' or 'release'")
    root = workspace.root.resolve()
    repository_paths = {
        name: project.repo.resolve() for name, project in workspace.projects.items()
    }
    metadata = {name: _read_project_metadata(path) for name, path in repository_paths.items()}
    package_projects = {
        _normalize_package(str(item["package"])): name
        for name, item in metadata.items()
        if item["package"]
    }
    origins: dict[str, str] = {}
    for name, path in repository_paths.items():
        origin = (
            normalize_origin(_git_value(path, "remote", "get-url", "origin"))
            if path.exists()
            else ""
        )
        if origin:
            origins[origin] = name

    records: list[RepositoryRecord] = []
    for project in sorted(workspace.projects):
        repo = repository_paths[project]
        item = metadata[project]
        exists = repo.is_dir()
        commit = _git_value(repo, "rev-parse", "HEAD").lower() if exists else ""
        origin = normalize_origin(_git_value(repo, "remote", "get-url", "origin")) if exists else ""
        branch = _git_value(repo, "symbolic-ref", "--short", "-q", "HEAD") if exists else ""
        dirty = (
            bool(_git_value(repo, "status", "--porcelain", "--untracked-files=normal"))
            if commit
            else False
        )
        tags = _tags_at_head(repo, commit) if commit else ()
        dependencies: set[InternalDependency] = set()
        for requirement in item["requirements"]:
            dependency = _resolve_dependency(
                requirement,
                package_projects,
                origins,
                repository_paths,
            )
            if dependency is not None:
                dependencies.add(dependency)
        layer = str(item["layer"] or _DEFAULT_LAYERS.get(project, ""))
        records.append(
            RepositoryRecord(
                project=project,
                path=_relative_path(repo, root),
                exists=exists,
                origin=origin,
                commit=commit,
                branch=branch,
                dirty=dirty,
                tags=tuple(sorted(tags)),
                package=str(item["package"]),
                version=str(item["version"]),
                requires_python=str(item["requires_python"]),
                layer=layer,
                internal_dependencies=tuple(sorted(dependencies)),
                schemas=tuple(sorted(item["schemas"])),
                external_locks=_discover_locks(repo, item["lock_paths"]) if exists else (),
            )
        )
    dag = tuple(
        DependencyNode(
            project=repo.project,
            depends_on=tuple(sorted({dep.project for dep in repo.internal_dependencies})),
        )
        for repo in records
    )
    configured_schemas = _configured_allowed_schemas(workspace)
    allowed_schemas = (
        configured_schemas
        if configured_schemas is not None
        else tuple(sorted({schema for repo in records for schema in repo.schemas}))
    )
    draft = StackManifest(
        schema_version=STACK_MANIFEST_SCHEMA_VERSION,
        mode=mode,
        created_at=_normalize_created_at(created_at),
        workspace_config_sha256=_sha256_file(workspace.config_path),
        repositories=tuple(records),
        dependency_dag=dag,
        allowed_schemas=allowed_schemas,
        release_ready=False,
        manifest_hash="",
    )
    preliminary = _validate_stack_manifest(draft, check_hash=False, check_readiness=False)
    ready = mode == "release" and preliminary.valid
    final_without_hash = replace(draft, release_ready=ready)
    final = replace(final_without_hash, manifest_hash=_manifest_hash(final_without_hash))
    result = validate_stack_manifest(final)
    if mode == "release" and not result.release_ready:
        raise StackManifestReleaseError(final, result)
    return final


def _governance_issue(
    mode: str,
    code: str,
    message: str,
    project: str = "",
) -> ValidationIssue:
    severity: Literal["error", "warning"] = "error" if mode == "release" else "warning"
    return ValidationIssue(severity=severity, code=code, message=message, project=project)


def _path_escapes(relative: str) -> bool:
    path = PurePosixPath(relative)
    return (
        path.is_absolute()
        or not relative
        or "\\" in relative
        or re.match(r"^[A-Za-z]:", relative) is not None
        or ".." in path.parts
    )


def _has_cycle(nodes: dict[str, set[str]]) -> bool:
    indegree = {name: 0 for name in nodes}
    dependents = {name: set() for name in nodes}
    for source, targets in nodes.items():
        for target in targets:
            if target in nodes:
                indegree[source] += 1
                dependents[target].add(source)
    ready = sorted(name for name, degree in indegree.items() if degree == 0)
    visited = 0
    while ready:
        current = ready.pop(0)
        visited += 1
        for dependent in sorted(dependents[current]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort()
    return visited != len(nodes)


def _validate_stack_manifest(
    manifest: StackManifest,
    *,
    check_hash: bool,
    check_readiness: bool,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    if manifest.schema_version != STACK_MANIFEST_SCHEMA_VERSION:
        issues.append(
            ValidationIssue("error", "SCHEMA_VERSION_INVALID", "Unsupported schema version")
        )
    if manifest.mode not in {"audit", "release"}:
        issues.append(ValidationIssue("error", "MODE_INVALID", "Mode must be audit or release"))
    if type(manifest.release_ready) is not bool:
        issues.append(
            ValidationIssue("error", "RELEASE_READY_INVALID", "release_ready must be a boolean")
        )
    try:
        normalized_time = _normalize_created_at(manifest.created_at)
        if normalized_time != manifest.created_at:
            issues.append(
                ValidationIssue(
                    "error", "CREATED_AT_NOT_CANONICAL", "created_at is not canonical UTC"
                )
            )
    except ValueError:
        issues.append(
            ValidationIssue(
                "error", "CREATED_AT_INVALID", "created_at is not timezone-aware ISO-8601"
            )
        )
    if not _SHA256.fullmatch(manifest.workspace_config_sha256):
        issues.append(
            ValidationIssue("error", "CONFIG_HASH_INVALID", "Workspace config hash is invalid")
        )
    if check_hash and manifest.manifest_hash != _manifest_hash(manifest):
        issues.append(
            ValidationIssue(
                "error", "MANIFEST_HASH_MISMATCH", "Manifest hash does not match content"
            )
        )

    if manifest.repositories != tuple(sorted(manifest.repositories, key=lambda item: item.project)):
        issues.append(
            ValidationIssue("error", "REPOSITORY_ORDER_INVALID", "Repositories are not canonical")
        )
    if manifest.dependency_dag != tuple(
        sorted(manifest.dependency_dag, key=lambda item: item.project)
    ):
        issues.append(ValidationIssue("error", "DAG_ORDER_INVALID", "DAG nodes are not canonical"))
    if manifest.allowed_schemas != tuple(sorted(set(manifest.allowed_schemas))):
        issues.append(
            ValidationIssue(
                "error", "ALLOWED_SCHEMA_ORDER_INVALID", "Allowed schemas are not canonical"
            )
        )
    if not manifest.allowed_schemas:
        issues.append(
            _governance_issue(
                manifest.mode, "ALLOWED_SCHEMA_MISSING", "No allowed schema set is recorded"
            )
        )
    for schema in manifest.allowed_schemas:
        if not schema.schema_id or not schema.version:
            issues.append(
                _governance_issue(
                    manifest.mode,
                    "ALLOWED_SCHEMA_INVALID",
                    "Allowed schema ID/version is missing",
                )
            )

    repositories: dict[str, RepositoryRecord] = {}
    packages: dict[str, str] = {}
    for repo in manifest.repositories:
        if repo.project in repositories:
            issues.append(
                ValidationIssue("error", "DUPLICATE_PROJECT", "Project is duplicated", repo.project)
            )
            continue
        repositories[repo.project] = repo
        if type(repo.exists) is not bool or type(repo.dirty) is not bool:
            issues.append(
                ValidationIssue(
                    "error",
                    "REPOSITORY_STATE_INVALID",
                    "exists/dirty must be booleans",
                    repo.project,
                )
            )
        if repo.tags != tuple(sorted(set(repo.tags))):
            issues.append(
                ValidationIssue(
                    "error", "TAG_ORDER_INVALID", "Tags are not canonical", repo.project
                )
            )
        if repo.internal_dependencies != tuple(sorted(set(repo.internal_dependencies))):
            issues.append(
                ValidationIssue(
                    "error",
                    "DEPENDENCY_ORDER_INVALID",
                    "Dependencies are not canonical",
                    repo.project,
                )
            )
        if repo.schemas != tuple(sorted(set(repo.schemas))):
            issues.append(
                ValidationIssue(
                    "error", "SCHEMA_ORDER_INVALID", "Schemas are not canonical", repo.project
                )
            )
        if repo.external_locks != tuple(sorted(set(repo.external_locks))):
            issues.append(
                ValidationIssue(
                    "error", "LOCK_ORDER_INVALID", "Locks are not canonical", repo.project
                )
            )
        if _path_escapes(repo.path):
            issues.append(
                _governance_issue(
                    manifest.mode,
                    "REPOSITORY_PATH_ESCAPE",
                    "Repository path escapes workspace",
                    repo.project,
                )
            )
        if not repo.exists:
            issues.append(
                _governance_issue(
                    manifest.mode, "REPOSITORY_MISSING", "Repository is missing", repo.project
                )
            )
        if not repo.origin:
            issues.append(
                _governance_issue(
                    manifest.mode, "ORIGIN_MISSING", "Origin is missing", repo.project
                )
            )
        elif normalize_origin(repo.origin) != repo.origin:
            issues.append(
                ValidationIssue(
                    "error", "ORIGIN_NOT_CANONICAL", "Origin is not canonical", repo.project
                )
            )
        if not _SHA40.fullmatch(repo.commit):
            issues.append(
                _governance_issue(
                    manifest.mode,
                    "COMMIT_INVALID",
                    "Commit must be a full lowercase SHA",
                    repo.project,
                )
            )
        if repo.dirty:
            issues.append(
                _governance_issue(
                    manifest.mode, "REPOSITORY_DIRTY", "Repository is dirty", repo.project
                )
            )
        if not repo.tags:
            issues.append(
                _governance_issue(
                    manifest.mode, "REPOSITORY_UNTAGGED", "HEAD has no tag", repo.project
                )
            )
        for tag in repo.tags:
            if not tag.name or type(tag.annotated) is not bool:
                issues.append(
                    ValidationIssue(
                        "error", "TAG_METADATA_INVALID", "Tag name/type is invalid", repo.project
                    )
                )
            if tag.target_commit != repo.commit:
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "TAG_COMMIT_MISMATCH",
                        f"Tag {tag.name} does not target HEAD",
                        repo.project,
                    )
                )
        if not repo.package or not repo.version or not repo.requires_python:
            issues.append(
                _governance_issue(
                    manifest.mode,
                    "PACKAGE_METADATA_MISSING",
                    "Package name/version/Python range is missing",
                    repo.project,
                )
            )
        else:
            try:
                Version(repo.version)
            except InvalidVersion:
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "PACKAGE_VERSION_INVALID",
                        "Package version is not PEP 440 compliant",
                        repo.project,
                    )
                )
            try:
                SpecifierSet(repo.requires_python)
            except InvalidSpecifier:
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "PYTHON_RANGE_INVALID",
                        "Python range is not a valid specifier",
                        repo.project,
                    )
                )
            if not any(tag.name in {repo.version, f"v{repo.version}"} for tag in repo.tags):
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "PACKAGE_TAG_MISMATCH",
                        "Package version has no matching HEAD tag",
                        repo.project,
                    )
                )
        normalized_package = _normalize_package(repo.package) if repo.package else ""
        if normalized_package:
            if normalized_package in packages:
                issues.append(
                    ValidationIssue(
                        "error",
                        "DUPLICATE_PACKAGE",
                        "Package belongs to multiple projects",
                        repo.project,
                    )
                )
            packages[normalized_package] = repo.project
        if not repo.layer:
            issues.append(
                _governance_issue(
                    manifest.mode,
                    "LAYER_MISSING",
                    "Repository dependency layer is not declared or known",
                    repo.project,
                )
            )
        elif repo.layer not in _LAYERS:
            issues.append(
                _governance_issue(
                    manifest.mode, "LAYER_INVALID", f"Unknown layer {repo.layer}", repo.project
                )
            )
        if not repo.schemas:
            issues.append(
                _governance_issue(
                    manifest.mode, "SCHEMA_MISSING", "No schema declaration", repo.project
                )
            )
        for schema in repo.schemas:
            if not schema.schema_id or not schema.version:
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "SCHEMA_INVALID",
                        "Schema ID/version is missing",
                        repo.project,
                    )
                )
            elif schema not in manifest.allowed_schemas:
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "SCHEMA_NOT_ALLOWED",
                        f"Schema {schema.schema_id}@{schema.version} is not allowed",
                        repo.project,
                    )
                )
        if not repo.external_locks:
            issues.append(
                _governance_issue(
                    manifest.mode, "LOCK_MISSING", "No external lock is declared", repo.project
                )
            )
        for lock in repo.external_locks:
            if _path_escapes(lock.path):
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "LOCK_PATH_ESCAPE",
                        f"Lock path escapes repository: {lock.path}",
                        repo.project,
                    )
                )
            if not _SHA256.fullmatch(lock.sha256):
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "LOCK_HASH_INVALID",
                        f"Lock is missing or has invalid hash: {lock.path}",
                        repo.project,
                    )
                )

    dag_nodes: dict[str, set[str]] = {}
    for node in manifest.dependency_dag:
        if node.project in dag_nodes:
            issues.append(
                ValidationIssue(
                    "error", "DUPLICATE_DAG_NODE", "DAG node is duplicated", node.project
                )
            )
            continue
        dag_nodes[node.project] = set(node.depends_on)
        if node.depends_on != tuple(sorted(set(node.depends_on))):
            issues.append(
                ValidationIssue(
                    "error",
                    "DAG_DEPENDENCY_ORDER_INVALID",
                    "DAG dependencies are not canonical",
                    node.project,
                )
            )
    if set(dag_nodes) != set(repositories):
        issues.append(
            _governance_issue(
                manifest.mode,
                "DAG_PROJECT_MISMATCH",
                "DAG projects do not match repository records",
            )
        )
    for project, repo in repositories.items():
        expected = {dep.project for dep in repo.internal_dependencies}
        if dag_nodes.get(project, set()) != expected:
            issues.append(
                _governance_issue(
                    manifest.mode,
                    "DAG_EDGE_MISMATCH",
                    "DAG edges do not match dependencies",
                    project,
                )
            )
        seen_dependencies: dict[str, InternalDependency] = {}
        for dependency in repo.internal_dependencies:
            if not dependency.package or not dependency.project:
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "DEPENDENCY_REQUIREMENT_INVALID",
                        f"Dependency requirement is not valid PEP 508: {dependency.ref!r}",
                        project,
                    )
                )
                continue
            existing = seen_dependencies.get(dependency.package)
            if existing is not None and existing != dependency:
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "DEPENDENCY_CONFLICT",
                        f"Conflicting refs for {dependency.package}",
                        project,
                    )
                )
            seen_dependencies[dependency.package] = dependency
            target = repositories.get(dependency.project)
            if target is None:
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "DEPENDENCY_TARGET_MISSING",
                        f"Dependency target {dependency.project} is absent",
                        project,
                    )
                )
                continue
            if dependency.package != _normalize_package(dependency.package):
                issues.append(
                    ValidationIssue(
                        "error",
                        "DEPENDENCY_PACKAGE_NOT_CANONICAL",
                        f"Dependency package {dependency.package} is not canonical",
                        project,
                    )
                )
            if target.package and dependency.package != _normalize_package(target.package):
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "DEPENDENCY_PACKAGE_MISMATCH",
                        f"Dependency package {dependency.package} does not match target",
                        project,
                    )
                )
            if dependency.ref_kind not in {"tag", "commit", "floating"}:
                issues.append(
                    ValidationIssue(
                        "error",
                        "DEPENDENCY_REF_KIND_INVALID",
                        f"Dependency {dependency.package} has invalid ref kind",
                        project,
                    )
                )
            elif dependency.ref_kind == "floating":
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "DEPENDENCY_REF_FLOATING",
                        f"Dependency {dependency.package} uses floating ref {dependency.ref}",
                        project,
                    )
                )
            elif not _SHA40.fullmatch(dependency.resolved_commit):
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "DEPENDENCY_REF_UNRESOLVED",
                        f"Dependency {dependency.package} cannot be resolved",
                        project,
                    )
                )
            elif dependency.resolved_commit != target.commit:
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "DEPENDENCY_COMMIT_MISMATCH",
                        f"Dependency {dependency.package} does not resolve to target HEAD",
                        project,
                    )
                )
            if dependency.ref_kind == "commit" and dependency.ref != dependency.resolved_commit:
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "DEPENDENCY_COMMIT_REF_MISMATCH",
                        f"Commit ref for {dependency.package} is inconsistent",
                        project,
                    )
                )
            if dependency.ref_kind == "tag" and not any(
                tag.name == dependency.ref for tag in target.tags
            ):
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "DEPENDENCY_TAG_MISMATCH",
                        f"Tag {dependency.ref} is not a target HEAD tag",
                        project,
                    )
                )
            if dependency.origin and target.origin and dependency.origin != target.origin:
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "DEPENDENCY_ORIGIN_MISMATCH",
                        f"Dependency {dependency.package} origin differs from target",
                        project,
                    )
                )
            if (
                repo.layer in _LAYERS
                and target.layer in _LAYERS
                and _LAYERS[repo.layer] < _LAYERS[target.layer]
            ):
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "DEPENDENCY_DIRECTION_INVALID",
                        f"{repo.layer} repository depends on downstream {target.layer}",
                        project,
                    )
                )
    for source, targets in dag_nodes.items():
        for target in targets:
            if target not in dag_nodes:
                issues.append(
                    _governance_issue(
                        manifest.mode,
                        "DAG_TARGET_MISSING",
                        f"DAG target {target} is absent",
                        source,
                    )
                )
    if _has_cycle(dag_nodes):
        issues.append(
            _governance_issue(
                manifest.mode, "DEPENDENCY_CYCLE", "Internal dependency DAG contains a cycle"
            )
        )

    issues.sort()
    valid = not any(issue.severity == "error" for issue in issues)
    expected_ready = manifest.mode == "release" and valid
    if check_readiness and manifest.release_ready != expected_ready:
        issues.append(
            ValidationIssue(
                "error", "RELEASE_READY_MISMATCH", "release_ready does not match validation"
            )
        )
        issues.sort()
        valid = False
        expected_ready = False
    return ValidationResult(valid=valid, release_ready=expected_ready, issues=tuple(issues))


def validate_stack_manifest(manifest: StackManifest) -> ValidationResult:
    return _validate_stack_manifest(manifest, check_hash=True, check_readiness=True)


def write_stack_manifest(path: Path | str, manifest: StackManifest) -> None:
    target = Path(path)
    result = validate_stack_manifest(manifest)
    if not result.valid:
        raise ValueError("Refusing to write an invalid stack manifest")
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite existing stack manifest: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_manifest_bytes(manifest) + b"\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="xb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise FileExistsError(
                f"Refusing to overwrite existing stack manifest: {target}"
            ) from exc
        temporary.unlink()
        temporary = None
        if os.name != "nt":
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_stack_manifest(path: Path | str) -> StackManifest:
    try:
        raw = Path(path).read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read StackManifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise TypeError("StackManifest root must be an object")
    manifest = StackManifest.from_dict(payload)
    if raw != canonical_manifest_bytes(manifest) + b"\n":
        raise ValueError("StackManifest file is not canonical JSON")
    return manifest
