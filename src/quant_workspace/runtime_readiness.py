"""Pinned per-project environments, without changing frozen release profiles."""

from __future__ import annotations

import hashlib
import importlib.metadata as importlib_metadata
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from quant_workspace.capabilities import _git

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

SCHEMA = "quant.runtime-profile/v1"
FIELDS = {"id", "repo", "revision", "lock", "lock_sha256", "distribution", "python", "environment"}
MAX_METADATA_BYTES = 4_000_000


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _relative(value: str) -> Path:
    if not isinstance(value, str) or not value or ":" in value or "\\" in value:
        raise ValueError("Profile paths must be confined relative paths")
    path = Path(value)
    if path.anchor or ".." in path.parts or path == Path("."):
        raise ValueError("Profile paths must be confined relative paths")
    return path


def _inside(root: Path, value: str) -> Path:
    path = root / _relative(value)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Profile path escapes its root")
    return path


def python_path(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def lock_requirements(text: str) -> list[Requirement]:
    """Support pip-compile exact versions and immutable Git commits, never resolve."""
    logical = text.replace("\r\n", "\n").replace("\\\n", " ")
    result = []
    for raw in logical.splitlines():
        line = re.split(r"\s+#", raw.strip(), maxsplit=1)[0]
        if not line or line.startswith("#"):
            continue
        line = re.split(r"\s+--hash=", line, maxsplit=1)[0].strip()
        try:
            requirement = Requirement(line)
            if requirement.url:
                uri = urlsplit(requirement.url)
                valid = (
                    uri.scheme == "git+https"
                    and uri.hostname is not None
                    and uri.username is None
                    and uri.password is None
                    and re.search(r"@[0-9a-f]{40}$", uri.path) is not None
                    and not uri.query
                    and not uri.fragment
                )
            else:
                specs = list(requirement.specifier)
                valid = (
                    len(specs) == 1 and specs[0].operator == "==" and "*" not in specs[0].version
                )
            if not valid:
                raise ValueError("not pinned")
        except ValueError as exc:
            raise ValueError(f"Unsupported or unlocked requirement: {line}") from exc
        result.append(requirement)
    if not result:
        raise ValueError("Empty locked requirements")
    return result


def _validate(profile: dict) -> None:
    if not isinstance(profile, dict) or set(profile) != {"schema_version", "projects"}:
        raise ValueError("Invalid runtime profile fields")
    projects = profile["projects"]
    if profile["schema_version"] != SCHEMA or not isinstance(projects, list) or not projects:
        raise ValueError("Unsupported or empty runtime profile")
    names, repos = set(), set()
    for item in projects:
        if not isinstance(item, dict) or set(item) != FIELDS:
            raise ValueError("Invalid runtime project fields")
        for key in ("id", "distribution"):
            if not isinstance(item[key], str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]*", item[key]
            ):
                raise ValueError("Invalid project identity")
        for key in ("repo", "lock", "environment"):
            _relative(item[key])
        if item["id"] in names or item["repo"].casefold() in repos:
            raise ValueError("Duplicate runtime project")
        names.add(item["id"])
        repos.add(item["repo"].casefold())
        for key, size in (("revision", 40), ("lock_sha256", 64)):
            if not isinstance(item[key], str) or not re.fullmatch(f"[0-9a-f]{{{size}}}", item[key]):
                raise ValueError("Profile requires immutable commit and lock digest")
        if not isinstance(item["python"], str) or not item["python"]:
            raise ValueError("Python version constraint required")
        SpecifierSet(item["python"])


def _source(item: dict, root: Path) -> tuple[Path, list[str]]:
    repo = _inside(root, item["repo"])
    issues = []
    try:
        if Path(_git(repo, "rev-parse", "--show-toplevel")).resolve() != repo.resolve():
            raise ValueError("Not an independent checkout")
        if _git(repo, "rev-parse", "HEAD") != item["revision"]:
            issues.append("revision_mismatch")
        if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
            issues.append("dirty_checkout")
        raw = _inside(repo, item["lock"]).read_bytes()
        if digest(raw) != item["lock_sha256"]:
            issues.append("lock_mismatch")
        lock_requirements(raw.decode("utf-8"))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        issues.append(f"source_unverifiable:{exc}")
    return repo, issues


def create_profile(workspace, projects, *, python=">=3.12,<3.13", environment=".venv") -> dict:
    _relative(environment)
    SpecifierSet(python)
    if not projects or len(projects) != len(set(projects)):
        raise ValueError("Select unique projects explicitly")
    rows = []
    for name in projects:
        repo = workspace.projects[name].repo.resolve()
        if not repo.is_relative_to(workspace.root) or repo == workspace.root:
            raise ValueError("Project must be within workspace root")
        project = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        item = {
            "id": name,
            "repo": repo.relative_to(workspace.root).as_posix(),
            "revision": _git(repo, "rev-parse", "HEAD"),
            "lock": "requirements.lock",
            "lock_sha256": digest((repo / "requirements.lock").read_bytes()),
            "distribution": project["name"],
            "python": python,
            "environment": environment,
        }
        _, issues = _source(item, workspace.root)
        if issues:
            raise ValueError(f"Cannot pin {name}: {issues}")
        rows.append(item)
    profile = {"schema_version": SCHEMA, "projects": rows}
    _validate(profile)
    return profile


def write_profile(path: Path, profile: dict) -> None:
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(profile, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _load(path: Path) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    if len(raw) > 1_000_000:
        raise ValueError("Runtime profile is too large")
    profile = json.loads(raw)
    _validate(profile)
    return profile, digest(raw)


def _read_pyvenv(environment: Path, executable: Path) -> dict:
    """Read venv identity without starting its interpreter or processing .pth files."""
    config = environment / "pyvenv.cfg"
    raw = config.read_bytes()
    if len(raw) > 65_536:
        raise ValueError("pyvenv.cfg is too large")
    fields = {}
    for number, raw_line in enumerate(raw.decode("utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"Malformed pyvenv.cfg line {number}")
        key, value = (part.strip() for part in line.split("=", 1))
        normalized = key.casefold()
        if not key or not value or normalized in fields:
            raise ValueError(f"Invalid pyvenv.cfg field on line {number}")
        fields[normalized] = value

    if fields.get("include-system-site-packages", "").casefold() != "false":
        raise ValueError("System site packages must be disabled")
    home_text = fields.get("home")
    if not home_text:
        raise ValueError("pyvenv.cfg home is required")
    home = Path(home_text)
    if not home.is_absolute() or not home.is_dir():
        raise ValueError("pyvenv.cfg home must be an existing absolute directory")
    if not executable.is_file():
        raise ValueError("Environment interpreter is missing")

    version_text = fields.get("version") or fields.get("version_info")
    match = re.fullmatch(r"([0-9]+)\.([0-9]+)(?:\.([0-9]+))?", version_text or "")
    if not match:
        raise ValueError("pyvenv.cfg requires numeric Python version metadata")
    try:
        Version(version_text)
    except InvalidVersion as exc:  # Defensive if packaging accepts less than the regex.
        raise ValueError("Invalid Python version metadata") from exc

    base_executable = None
    if configured := fields.get("executable"):
        candidate = Path(configured)
        if (
            not candidate.is_absolute()
            or not candidate.is_file()
            or candidate.parent.resolve() != home.resolve()
        ):
            raise ValueError("pyvenv.cfg executable does not match home")
        base_executable = str(candidate.resolve())

    return {
        "fields": fields,
        "version": version_text,
        "major_minor": f"{match.group(1)}.{match.group(2)}",
        "home": str(home.resolve()),
        "base_executable": base_executable,
        "implementation": fields.get("implementation"),
    }


def _site_packages(environment: Path, major_minor: str, executable: Path) -> list[Path]:
    if executable.parent.name.casefold() == "scripts":
        candidates = [environment / "Lib" / "site-packages"]
    else:
        candidates = [
            environment / "lib" / f"python{major_minor}" / "site-packages",
            environment / "lib64" / f"python{major_minor}" / "site-packages",
        ]
    paths = []
    for candidate in candidates:
        if candidate.is_dir() and candidate.resolve() not in {path.resolve() for path in paths}:
            paths.append(candidate)
    if not paths:
        raise ValueError("Environment site-packages directory is missing")
    return paths


def _read_environment(environment: Path, executable: Path) -> dict:
    identity = _read_pyvenv(environment, executable)
    marker = default_environment()
    marker.update(
        {
            "python_full_version": identity["version"],
            "python_version": identity["major_minor"],
            "extra": "",
        }
    )
    if identity["implementation"]:
        implementation = identity["implementation"].casefold()
        marker["implementation_name"] = implementation
        marker["platform_python_implementation"] = identity["implementation"]
        if implementation == "cpython":
            marker["implementation_version"] = identity["version"]

    distributions = []
    metadata_size = 0
    for distribution in importlib_metadata.distributions(
        path=[
            str(path) for path in _site_packages(environment, identity["major_minor"], executable)
        ]
    ):
        name = distribution.metadata.get("Name")
        version = distribution.version
        requires_python = distribution.metadata.get("Requires-Python")
        if not name or not version:
            raise ValueError("Installed distribution lacks name or version metadata")
        direct_text = distribution.read_text("direct_url.json")
        requires = distribution.requires or []
        metadata_size += (
            len(name)
            + len(version)
            + len(requires_python or "")
            + sum(map(len, requires))
            + len(direct_text or "")
        )
        if metadata_size > MAX_METADATA_BYTES:
            raise ValueError("Environment metadata is too large")
        distributions.append(
            {
                "name": name,
                "version": version,
                "requires_python": requires_python,
                "requires": requires,
                "direct_url": json.loads(direct_text) if direct_text else None,
            }
        )
    return {
        "version": identity["version"],
        "prefix": str(environment.resolve()),
        "base_prefix": identity["home"],
        "marker_environment": marker,
        "distributions": distributions,
        "include_system_site_packages": False,
        "interpreter": str(executable.resolve()),
        "base_executable": identity["base_executable"],
        "implementation": identity["implementation"],
    }


def _version_matches(version: str, specifier: SpecifierSet) -> bool | None:
    """Return None when major.minor metadata cannot decide a patch-sensitive constraint."""
    parsed = Version(version)
    if parsed.micro or version.count(".") >= 2:
        return version in specifier
    for constraint in specifier:
        raw_boundary = constraint.version
        wildcard = raw_boundary.endswith(".*")
        boundary = Version(raw_boundary.removesuffix(".*"))
        if (boundary.major, boundary.minor) != (parsed.major, parsed.minor):
            continue
        plain_zero = (
            boundary.micro == 0
            and boundary.epoch == 0
            and boundary.pre is None
            and boundary.post is None
            and boundary.dev is None
            and boundary.local is None
        )
        safe_wildcard = wildcard and len(boundary.release) <= 2
        safe_same_family = safe_wildcard or (
            constraint.operator in {">=", "<", "~="} and plain_zero
        )
        if not safe_same_family:
            return None
    return Version(f"{parsed.major}.{parsed.minor}.0") in specifier


def _marker_applies(marker, probe: dict) -> bool | None:
    text = str(marker)
    full_version = probe["version"].count(".") >= 2
    implementation = (probe.get("implementation") or "").casefold()
    if not full_version and "python_full_version" in text:
        return None
    if "implementation_version" in text and (not full_version or implementation != "cpython"):
        return None
    if not implementation and any(
        field in text for field in ("implementation_name", "platform_python_implementation")
    ):
        return None
    return marker.evaluate(probe["marker_environment"])


def _environment_issues(item: dict, repo: Path, probe: dict) -> list[str]:
    issues = []
    python_match = _version_matches(probe["version"], SpecifierSet(item["python"]))
    if python_match is None:
        issues.append("metadata_insufficient:python_patch_version")
    elif not python_match:
        issues.append("python_version_mismatch")
    environment = (repo / item["environment"]).resolve()
    executable = python_path(environment).resolve()
    if (
        Path(probe["prefix"]).resolve() != environment
        or probe["prefix"] == probe["base_prefix"]
        or Path(probe["interpreter"]).resolve() != executable
        or probe.get("include_system_site_packages") is not False
    ):
        issues.append("environment_identity_mismatch")
    distributions = {}
    for dist in probe["distributions"]:
        name = canonicalize_name(dist["name"])
        if name in distributions:
            issues.append(f"duplicate_distribution:{name}")
        distributions[name] = dist
    active_lock = []
    for requirement in lock_requirements((repo / item["lock"]).read_text(encoding="utf-8")):
        if requirement.marker:
            applies = _marker_applies(requirement.marker, probe)
            if applies is None:
                active_lock.append(requirement)
                issues.append(
                    f"metadata_insufficient:lock_marker:{canonicalize_name(requirement.name)}"
                )
                continue
            if not applies:
                continue
        active_lock.append(requirement)
        name = canonicalize_name(requirement.name)
        installed = distributions.get(name)
        if installed is None:
            issues.append(f"locked_package_missing:{name}")
        elif requirement.url:
            expected = requirement.url.rsplit("@", 1)[1]
            direct = installed.get("direct_url") or {}
            expected_url = requirement.url.removeprefix("git+").rsplit("@", 1)[0]
            vcs = direct.get("vcs_info")
            if (
                set(direct) != {"url", "vcs_info"}
                or not isinstance(vcs, dict)
                or set(vcs) != {"vcs", "requested_revision", "commit_id"}
                or vcs.get("vcs") != "git"
                or vcs.get("requested_revision") != expected
                or vcs.get("commit_id") != expected
                or direct.get("url") != expected_url
            ):
                issues.append(f"locked_git_mismatch:{name}")
        elif installed["version"] not in requirement.specifier:
            issues.append(f"locked_version_mismatch:{name}")
    own = distributions.get(canonicalize_name(item["distribution"]), {})
    direct = own.get("direct_url") or {}
    uri = urlsplit(direct.get("url", ""))
    if (
        set(direct) != {"url", "dir_info"}
        or direct.get("dir_info") != {"editable": True}
        or uri.scheme != "file"
        or uri.netloc not in ("", "localhost")
        or Path(url2pathname(uri.path)).resolve() != repo.resolve()
    ):
        issues.append("editable_source_mismatch")

    active_names = {canonicalize_name(requirement.name) for requirement in active_lock}
    permitted_names = active_names | {canonicalize_name(item["distribution"])}
    for name in sorted(distributions.keys() - permitted_names):
        distribution = distributions[name]
        if name != "pip" or distribution.get("direct_url") is not None:
            issues.append(f"unlocked_distribution:{name}")
    for name, dist in distributions.items():
        if dist.get("requires_python"):
            matches = _version_matches(probe["version"], SpecifierSet(dist["requires_python"]))
            if matches is None:
                issues.append(f"metadata_insufficient:requires_python:{name}")
            elif not matches:
                issues.append(f"incompatible_python:{name}")
        for raw in dist["requires"]:
            requirement = Requirement(raw)
            dependency = canonicalize_name(requirement.name)
            if requirement.marker:
                applies = _marker_applies(requirement.marker, probe)
                if applies is None:
                    issues.append(f"metadata_insufficient:dependency_marker:{name}:{dependency}")
                    continue
                if not applies:
                    continue
            target = distributions.get(dependency)
            if target is None:
                issues.append(f"missing_dependency:{name}:{dependency}")
            elif requirement.specifier and target["version"] not in requirement.specifier:
                issues.append(f"incompatible_dependency:{name}:{dependency}")
    return issues


def _bootstrap_tools(item: dict, repo: Path, probe: dict) -> list[dict]:
    pip_locked = False
    for requirement in lock_requirements((repo / item["lock"]).read_text(encoding="utf-8")):
        if canonicalize_name(requirement.name) != "pip":
            continue
        applies = _marker_applies(requirement.marker, probe) if requirement.marker else True
        if applies is None:
            pip_locked = None
            break
        if applies:
            pip_locked = True
    tools = []
    for distribution in probe["distributions"]:
        if canonicalize_name(distribution["name"]) == "pip":
            tools.append(
                {
                    "name": "pip",
                    "version": distribution["version"],
                    "locked": pip_locked,
                    "lock_applicability_verified": pip_locked is not None,
                    "content_authenticated": False,
                }
            )
    return tools


def check_runtime(path: Path, root: Path) -> dict:
    profile, sha = _load(path)
    rows = []
    for item in profile["projects"]:
        repo, issues = _source(item, Path(root).resolve())
        version = None
        environment_metadata = None
        bootstrap_tools = []
        if not issues:
            environment = _inside(repo, item["environment"])
            executable = python_path(environment)
            if not executable.is_file():
                issues.append("environment_missing")
            else:
                try:
                    probed = _read_environment(environment, executable)
                    version = probed["version"]
                    environment_metadata = {
                        "interpreter": probed["interpreter"],
                        "home": probed["base_prefix"],
                        "base_executable": probed["base_executable"],
                        "implementation": probed["implementation"],
                        "include_system_site_packages": probed["include_system_site_packages"],
                    }
                    bootstrap_tools = _bootstrap_tools(item, repo, probed)
                    issues.extend(_environment_issues(item, repo, probed))
                    _, after = _source(item, Path(root).resolve())
                    issues.extend(after)
                except (
                    OSError,
                    KeyError,
                    TypeError,
                    ValueError,
                    subprocess.SubprocessError,
                ) as exc:
                    issues.append(f"environment_unverifiable:{exc}")
        rows.append(
            {
                "id": item["id"],
                "revision": item["revision"],
                "python_version": version,
                "environment": environment_metadata,
                "bootstrap_tools": bootstrap_tools,
                "status": "blocked" if issues else "ready",
                "issues": sorted(set(issues)),
            }
        )
    profile_changed = digest(Path(path).read_bytes()) != sha
    return {
        "schema_version": "quant.runtime-readiness/v1",
        "profile_sha256": sha,
        "status": "ready"
        if not profile_changed and all(r["status"] == "ready" for r in rows)
        else "blocked",
        "issues": ["profile_changed_during_check"] if profile_changed else [],
        "projects": rows,
        "claims": {
            "integration_verified": False,
            "market_data_certified": False,
            "release_verified": False,
            "dependency_content_authenticated": False,
            "environment_metadata_authenticated": False,
            "interpreter_binary_authenticated": False,
            "target_interpreter_executed": False,
        },
    }


def _output_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _write_step_log(
    environment: Path,
    index: int,
    command: list[str],
    status: str,
    *,
    returncode: int | None = None,
    stdout="",
    stderr="",
    error_type: str | None = None,
    error: str | None = None,
    issues: list[str] | None = None,
) -> None:
    payload = {
        "schema_version": "quant.runtime-bootstrap-step/v1",
        "step": index,
        "command": command,
        "status": status,
        "returncode": returncode,
        "stdout": _output_text(stdout),
        "stderr": _output_text(stderr),
        "error_type": error_type,
        "error": error,
        "issues": issues or [],
    }
    target = environment / f"bootstrap-{index}.log"
    temporary = environment / f"bootstrap-{index}.log.tmp"
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(target)


def bootstrap(path: Path, root: Path, project: str, *, execute: bool = False) -> dict:
    profile, sha = _load(path)
    item = next((p for p in profile["projects"] if p["id"] == project), None)
    if item is None:
        raise ValueError("Project is not in runtime profile")
    repo, issues = _source(item, Path(root).resolve())
    if issues:
        raise ValueError(f"Source does not match profile: {issues}")
    environment = _inside(repo, item["environment"])
    executable = python_path(environment)
    if environment.exists() or environment.is_symlink():
        raise ValueError("Environment already exists; choose a new profile environment")
    try:
        _git(repo, "check-ignore", "--quiet", "--", f"{item['environment']}/bootstrap-0.log")
    except subprocess.SubprocessError as exc:
        raise ValueError("New environment must be Git-ignored before creating a profile") from exc
    if ".".join(map(str, sys.version_info[:3])) not in SpecifierSet(item["python"]):
        raise ValueError("Invoke bootstrap with a Python matching the profile")
    commands = [
        [sys.executable, "-I", "-m", "venv", str(environment)],
        [
            str(executable),
            "-I",
            "-m",
            "pip",
            "install",
            "--no-deps",
            "-r",
            str(repo / item["lock"]),
        ],
        [
            str(executable),
            "-I",
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            "-e",
            str(repo),
        ],
        [str(executable), "-I", "-m", "pip", "check"],
    ]
    if execute:
        # Exclude concurrent bootstraps; leave the new directory and logs on failure.
        environment.mkdir(parents=False, exist_ok=False)
        for index, command in enumerate(commands):
            _, current_issues = _source(item, Path(root).resolve())
            profile_changed = digest(Path(path).read_bytes()) != sha
            if current_issues or profile_changed:
                issues = list(current_issues)
                if profile_changed:
                    issues.append("profile_changed_during_bootstrap")
                _write_step_log(environment, index, command, "blocked", issues=issues)
                raise ValueError("Profile or source changed during bootstrap; environment retained")
            try:
                result = subprocess.run(
                    command,
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=900,
                    check=False,
                )
            except KeyboardInterrupt as exc:
                _write_step_log(
                    environment,
                    index,
                    command,
                    "interrupted",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                raise
            except subprocess.TimeoutExpired as exc:
                _write_step_log(
                    environment,
                    index,
                    command,
                    "timed_out",
                    stdout=exc.stdout,
                    stderr=exc.stderr,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                raise ValueError(
                    f"Bootstrap step {index} timed out; incomplete environment retained"
                ) from exc
            except (OSError, subprocess.SubprocessError) as exc:
                _write_step_log(
                    environment,
                    index,
                    command,
                    "failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                raise ValueError(
                    f"Bootstrap step {index} failed; incomplete environment retained"
                ) from exc
            _write_step_log(
                environment,
                index,
                command,
                "succeeded" if result.returncode == 0 else "failed",
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
            if result.returncode:
                raise ValueError(f"Bootstrap step {index} failed; incomplete environment retained")
        report = check_runtime(path, root)
        if report["profile_sha256"] != sha or report["issues"]:
            raise ValueError("Profile changed during bootstrap; environment retained")
        row = next(r for r in report["projects"] if r["id"] == project)
        if row["status"] != "ready":
            raise ValueError(f"Environment did not pass verification: {row['issues']}")
    return {
        "schema_version": "quant.runtime-bootstrap/v1",
        "profile_sha256": sha,
        "project": project,
        "executed": execute,
        "commands": commands,
    }
