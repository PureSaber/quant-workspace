"""Inspectable capability declarations and read-only local source inventory.

Presence of a checkout is deliberately not a runtime, research or release gate.
"""

from __future__ import annotations

import json
import re
import subprocess
from importlib.resources import files
from pathlib import Path, PurePosixPath


def load_capabilities() -> dict:
    catalog = json.loads(
        files("quant_workspace").joinpath("capabilities.json").read_text(encoding="utf-8")
    )
    validate_capabilities(catalog)
    return catalog


def validate_capabilities(catalog: dict) -> None:
    if catalog.get("schema_version") != "quant.capabilities/v1":
        raise ValueError("Unsupported capability catalog schema")
    projects = catalog.get("projects", [])
    names = [project["id"] for project in projects]
    if not names or len(names) != len(set(names)):
        raise ValueError("Capability project IDs must be non-empty and unique")
    for project in projects:
        if not re.fullmatch(r"[a-z][a-z0-9-]*", project["id"]):
            raise ValueError("Capability project IDs must be repository names")
        for field in ("layer", "data_status"):
            if not isinstance(project.get(field), str) or not project[field].strip():
                raise ValueError(f"Missing project {field}: {project['id']}")
        for field in ("assets", "capabilities", "entrypoints", "contracts", "gaps", "evidence"):
            values = project.get(field)
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(value, str) or not value.strip() for value in values)
            ):
                raise ValueError(f"Missing project {field}: {project['id']}")
        for evidence in project["evidence"]:
            path = PurePosixPath(evidence)
            if path.is_absolute() or ".." in path.parts or ":" in evidence or "\\" in evidence:
                raise ValueError("Evidence paths must stay within their repository")
    for scope in catalog["release_scopes"].values():
        members = scope["projects"]
        if len(members) != len(set(members)) or set(members) - set(names):
            raise ValueError("Release scope has duplicate or unknown projects")
    for link in catalog["relationships"]:
        if link["from"] not in names or link["to"] not in names or not link["contract"]:
            raise ValueError("Relationships require known endpoints and a contract")


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=True,
    )
    return result.stdout.strip()


def source_inventory(catalog: dict, root: Path) -> dict:
    """Check every declared repo and evidence path without importing its code."""
    validate_capabilities(catalog)
    root = root.resolve()
    rows = []
    for project in catalog["projects"]:
        repo = (root / project["id"]).resolve()
        row = {
            "id": project["id"],
            "path": str(repo),
            "source_status": "missing",
            "revision": None,
            "dirty": None,
            "missing_evidence": [],
            "release_scopes": [
                name
                for name, scope in catalog["release_scopes"].items()
                if project["id"] in scope["projects"]
            ],
        }
        if not repo.is_relative_to(root):
            row["source_status"] = "outside_workspace"
        elif repo.is_dir():
            try:
                if Path(_git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
                    raise ValueError("Directory is not an independent Git checkout")
                revision = _git(repo, "rev-parse", "HEAD")
                if not re.fullmatch(r"[0-9a-f]{40}", revision):
                    raise ValueError("Git HEAD is not a full commit identity")
                row["revision"] = revision
                row["dirty"] = bool(_git(repo, "status", "--porcelain=v1", "--untracked-files=all"))
                row["missing_evidence"] = [
                    evidence
                    for evidence in project["evidence"]
                    if not (repo / evidence).resolve().is_relative_to(repo)
                    or not (repo / evidence).is_file()
                ]
                row["source_status"] = "evidence_missing" if row["missing_evidence"] else "present"
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                row["source_status"] = "unverifiable"
                row["error"] = str(exc)
        rows.append(row)
    return {
        "schema_version": "quant.source-inventory/v1",
        "catalog_as_of": catalog["as_of"],
        "root": str(root),
        "source_inventory_complete": all(row["source_status"] == "present" for row in rows),
        "claims": {
            "runtime_verified": False,
            "integration_verified": False,
            "market_data_certified": False,
            "release_verified": False,
        },
        "projects": rows,
    }
