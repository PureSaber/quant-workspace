from __future__ import annotations

import os
from pathlib import Path

import yaml

from quant_workspace.models import ProjectPaths, Workspace

_PATH_KEYS = ("outputs", "state", "data", "notes", "reports")


def _resolve_root(raw_root: str | Path, config_path: Path) -> Path:
    root = Path(os.path.expandvars(str(raw_root)))
    if not root.is_absolute():
        root = (config_path.parent / root).resolve()
    return root.resolve()


def _project_paths(name: str, root: Path, entry: dict) -> ProjectPaths:
    repo_name = str(entry.get("repo", name))
    repo = (root / repo_name).resolve()

    kwargs: dict = {"name": name, "repo": repo}
    extra: dict[str, Path] = {}

    for key, rel in entry.items():
        if key == "repo":
            continue
        path = (repo / str(rel)).resolve() if not Path(str(rel)).is_absolute() else Path(str(rel))
        if key in _PATH_KEYS:
            kwargs[key] = path
        elif key not in ("name",):
            extra[key] = path

    return ProjectPaths(**kwargs, extra=extra)


def load_workspace(config_path: Path | str, *, root_override: Path | str | None = None) -> Workspace:
    config_path = Path(config_path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    env_root = os.environ.get("QUANT_WORKSPACE_ROOT")
    root_raw = root_override or env_root or raw.get("root", config_path.parent.parent)
    root = _resolve_root(str(root_raw), config_path)

    projects_cfg = raw.get("projects") or {}
    projects = {
        name: _project_paths(name, root, entry if isinstance(entry, dict) else {"repo": entry})
        for name, entry in projects_cfg.items()
    }
    return Workspace(root=root, config_path=config_path, projects=projects)


def resolve_path(
    config_path: Path | str,
    project: str,
    key: str = "repo",
    *,
    root_override: Path | str | None = None,
) -> Path:
    return load_workspace(config_path, root_override=root_override).path(project, key)
