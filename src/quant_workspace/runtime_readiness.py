"""Pinned per-project environments, without changing frozen release profiles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

from quant_workspace.capabilities import _git

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

SCHEMA = "quant.runtime-profile/v1"
FIELDS = {"id", "repo", "revision", "lock", "lock_sha256", "distribution", "python", "environment"}
PROBE = r"""
import importlib.metadata as m, json, os, platform, sys
v = sys.implementation.version
implementation_version = f"{v.major}.{v.minor}.{v.micro}"
if v.releaselevel != "final":
    implementation_version += v.releaselevel[0] + str(v.serial)
environment = {
    "implementation_name": sys.implementation.name, "implementation_version": implementation_version,
    "os_name": os.name, "platform_machine": platform.machine(), "platform_release": platform.release(),
    "platform_system": platform.system(), "platform_version": platform.version(),
    "python_full_version": platform.python_version(), "python_version": ".".join(platform.python_version_tuple()[:2]),
    "platform_python_implementation": platform.python_implementation(), "sys_platform": sys.platform, "extra": ""
}
distributions = []
for d in m.distributions():
    direct = d.read_text("direct_url.json")
    distributions.append({"name": d.metadata["Name"], "version": d.version,
                          "requires": d.requires or [], "direct_url": json.loads(direct) if direct else None})
print(json.dumps({"version": platform.python_version(), "prefix": sys.prefix,
                  "base_prefix": sys.base_prefix, "marker_environment": environment,
                  "distributions": distributions}))
"""


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


def _probe(executable: Path, repo: Path) -> dict:
    result = subprocess.run(
        [str(executable), "-I", "-c", PROBE],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
    )
    if len(result.stdout) > 4_000_000:
        raise ValueError("Environment metadata is too large")
    return json.loads(result.stdout)


def _environment_issues(item: dict, repo: Path, probe: dict) -> list[str]:
    issues = []
    if probe["version"] not in SpecifierSet(item["python"]):
        issues.append("python_version_mismatch")
    if (
        Path(probe["prefix"]).resolve() != (repo / item["environment"]).resolve()
        or probe["prefix"] == probe["base_prefix"]
    ):
        issues.append("environment_identity_mismatch")
    distributions = {}
    for dist in probe["distributions"]:
        name = canonicalize_name(dist["name"])
        if name in distributions:
            issues.append(f"duplicate_distribution:{name}")
        distributions[name] = dist
    marker = probe["marker_environment"]
    for requirement in lock_requirements((repo / item["lock"]).read_text(encoding="utf-8")):
        if requirement.marker and not requirement.marker.evaluate(marker):
            continue
        name = canonicalize_name(requirement.name)
        installed = distributions.get(name)
        if installed is None:
            issues.append(f"locked_package_missing:{name}")
        elif requirement.url:
            expected = requirement.url.rsplit("@", 1)[1]
            direct = installed.get("direct_url") or {}
            expected_url = requirement.url.removeprefix("git+").rsplit("@", 1)[0]
            if (
                direct.get("vcs_info", {}).get("commit_id") != expected
                or direct.get("url") != expected_url
            ):
                issues.append(f"locked_git_mismatch:{name}")
        elif installed["version"] not in requirement.specifier:
            issues.append(f"locked_version_mismatch:{name}")
    own = distributions.get(canonicalize_name(item["distribution"]), {})
    direct = own.get("direct_url") or {}
    uri = urlsplit(direct.get("url", ""))
    if (
        uri.scheme != "file"
        or uri.netloc not in ("", "localhost")
        or not direct.get("dir_info", {}).get("editable")
        or Path(url2pathname(uri.path)).resolve() != repo.resolve()
    ):
        issues.append("editable_source_mismatch")
    for name, dist in distributions.items():
        for raw in dist["requires"]:
            requirement = Requirement(raw)
            if requirement.marker and not requirement.marker.evaluate(marker):
                continue
            dependency = canonicalize_name(requirement.name)
            target = distributions.get(dependency)
            if target is None:
                issues.append(f"missing_dependency:{name}:{dependency}")
            elif requirement.specifier and target["version"] not in requirement.specifier:
                issues.append(f"incompatible_dependency:{name}:{dependency}")
    return issues


def check_runtime(path: Path, root: Path) -> dict:
    profile, sha = _load(path)
    rows = []
    for item in profile["projects"]:
        repo, issues = _source(item, Path(root).resolve())
        version = None
        if not issues:
            environment = _inside(repo, item["environment"])
            executable = python_path(environment)
            if not executable.is_file():
                issues.append("environment_missing")
            else:
                try:
                    probed = _probe(executable, repo)
                    version = probed["version"]
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
        },
    }


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
            if current_issues or digest(Path(path).read_bytes()) != sha:
                raise ValueError("Profile or source changed during bootstrap; environment retained")
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
            (environment / f"bootstrap-{index}.log").write_text(
                result.stdout + result.stderr, encoding="utf-8"
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
