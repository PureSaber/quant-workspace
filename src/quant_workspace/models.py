from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    name: str
    repo: Path
    outputs: Path | None = None
    state: Path | None = None
    data: Path | None = None
    notes: Path | None = None
    reports: Path | None = None
    extra: dict[str, Path] = field(default_factory=dict)

    def get(self, key: str) -> Path:
        value = getattr(self, key, None)
        if value is not None:
            return value
        if key in self.extra:
            return self.extra[key]
        raise KeyError(f"Project {self.name!r} has no path key {key!r}")


@dataclass(frozen=True)
class Workspace:
    root: Path
    config_path: Path
    projects: dict[str, ProjectPaths]

    def path(self, project: str, key: str = "repo") -> Path:
        if project not in self.projects:
            known = ", ".join(sorted(self.projects))
            raise KeyError(f"Unknown project {project!r}. Known: {known}")
        return self.projects[project].get(key)

    def lab_workspace_yaml(self, db_path: str | Path | None = None) -> dict:
        """Build quant-lab compatible workspace config."""
        entries = []
        for name, proj in sorted(self.projects.items()):
            if proj.outputs is not None:
                entries.append({"name": name, "outputs": str(proj.outputs)})
        if db_path is not None:
            db = str(db_path)
        elif "quant-lab" in self.projects:
            db = str(self.path("quant-lab", "state") / "experiments.db")
        else:
            db = str(self.root / "quant-lab" / "state" / "experiments.db")
        return {"db_path": db, "projects": entries}
