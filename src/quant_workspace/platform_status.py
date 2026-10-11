"""Read-only platform status, preserving distinct evidence and runtime scopes."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from quant_workspace.capabilities import source_inventory, validate_capabilities
from quant_workspace.delivery import (
    _load_json,
    current_release,
    load_candidate,
    verify_acceptance,
)
from quant_workspace.runtime_readiness import _load as load_profile
from quant_workspace.runtime_readiness import check_runtime

SCHEMA = "quant.platform-status/v1"


def workflow_status(snapshot: dict) -> dict:
    """Evaluate explicitly required jobs, never a workflow's aggregate green badge.

    The snapshot is a caller-supplied GitHub API export, not an authenticated fetch.
    Both run and complete job-list responses must refer to the pinned head and run.
    """
    fields = {"expected_head_sha", "required_jobs", "run", "jobs"}
    if not isinstance(snapshot, dict) or set(snapshot) != fields:
        raise ValueError("Workflow snapshot fields are invalid")
    head = snapshot["expected_head_sha"]
    names = snapshot["required_jobs"]
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("Workflow requires a full expected head SHA")
    if (
        not isinstance(names, list)
        or not names
        or any(not isinstance(name, str) or not name for name in names)
        or len(set(names)) != len(names)
    ):
        raise ValueError("Required workflow job names must be nonempty and unique")
    run, listing = snapshot["run"], snapshot["jobs"]
    if not isinstance(run, dict) or not isinstance(listing, dict):
        raise TypeError("Workflow run and jobs must be objects")
    jobs = listing.get("jobs")
    if not isinstance(jobs, list) or listing.get("total_count") != len(jobs):
        raise ValueError("Workflow jobs must include all pages")
    if type(run.get("id")) is not int or run["id"] <= 0:
        raise ValueError("Workflow run ID is invalid")
    if type(run.get("run_attempt")) is not int or run["run_attempt"] <= 0:
        raise ValueError("Workflow attempt is invalid")
    issues = []
    if run.get("head_sha") != head:
        issues.append("run_head_mismatch")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        issues.append("run_not_successful")
    by_name: dict[str, dict] = {}
    ids = set()
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get("name"), str):
            raise TypeError("Invalid workflow job")
        if job["name"] in by_name or job.get("id") in ids:
            raise ValueError("Duplicate workflow job")
        if type(job.get("id")) is not int or job["id"] <= 0:
            raise ValueError("Invalid workflow job ID")
        ids.add(job["id"])
        by_name[job["name"]] = job
        if job.get("run_id") != run["id"] or job.get("head_sha") != head:
            issues.append(f"job_identity_mismatch:{job['name']}")
        if job.get("run_attempt") != run.get("run_attempt"):
            issues.append(f"job_attempt_mismatch:{job['name']}")
    rows = []
    for name in names:
        job = by_name.get(name)
        state = "missing" if job is None else job.get("conclusion") or job.get("status")
        if job is None or job.get("status") != "completed" or state != "success":
            issues.append(f"required_job_not_passed:{name}:{state}")
        rows.append({"name": name, "state": state})
    return {
        "status": "passed" if not issues else "blocked",
        "run_id": run["id"],
        "head_sha": head,
        "required_jobs": rows,
        "issues": issues,
        "source_authenticated": False,
        "evidence_kind": "caller_supplied_github_api_export",
    }


def platform_status(
    catalog: dict,
    source_root: Path,
    *,
    profile_path: Path | None = None,
    candidate_path: Path | None = None,
    evidence_path: Path | None = None,
    prepared_root: Path | None = None,
    state_dir: Path | None = None,
    workflow_snapshot: Path | None = None,
) -> dict:
    """Re-read actual source/environment/output bytes without running acceptance.

    Historical acceptance and current source identity remain separate. Installing a
    package or switching a release pointer does not identify a running application.
    """
    validate_capabilities(catalog)
    if evidence_path is not None and (candidate_path is None or prepared_root is None):
        raise ValueError("Acceptance evidence requires candidate and prepared root")
    source_root = Path(source_root).resolve()
    inventory = source_inventory(catalog, source_root)
    ids = {row["id"] for row in catalog["projects"]}
    profile = None
    runtime = {"status": "not_checked", "projects": []}
    if profile_path is not None:
        profile, _ = load_profile(profile_path)
        if {row["id"] for row in profile["projects"]} - ids:
            raise ValueError("Runtime profile contains projects outside the catalog")
        runtime = check_runtime(profile_path, source_root)
    acceptance = {"status": "not_run", "candidate_sha256": None, "case_kinds": []}
    candidate = None
    if candidate_path is not None:
        candidate, _ = load_candidate(candidate_path)
        if {row["id"] for row in candidate["projects"]} - ids:
            raise ValueError("Candidate contains projects outside the catalog")
        acceptance["candidate_sha256"] = candidate["candidate_sha256"]
        if evidence_path is not None:
            try:
                evidence = verify_acceptance(candidate, evidence_path, prepared_root)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                acceptance.update(status="blocked", reason=str(exc))
            else:
                acceptance.update(
                    status="verified_in_combination",
                    acceptance_sha256=evidence["acceptance_sha256"],
                    case_kinds=sorted({c["kind"] for c in candidate["acceptance_suite"]["cases"]}),
                    cases=[
                        {"id": c["id"], "kind": c["kind"]}
                        for c in candidate["acceptance_suite"]["cases"]
                    ],
                    boundary="Revalidated recorded outputs and environment; commands not rerun; local evidence is not externally signed.",
                )
    active = {"status": "not_checked", "candidate_sha256": None}
    if state_dir is not None:
        try:
            pointer = current_release(state_dir)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            active.update(status="blocked", reason=str(exc))
        else:
            active.update(
                status="pointer_recorded" if pointer else "unmanaged",
                candidate_sha256=pointer["candidate_sha256"] if pointer else None,
            )
            if pointer is not None and candidate is not None:
                active["matches_candidate"] = (
                    pointer["candidate_sha256"] == candidate["candidate_sha256"]
                )
    workflow = {"status": "not_checked"}
    if workflow_snapshot is not None:
        snapshot, _ = _load_json(workflow_snapshot)
        workflow = workflow_status(snapshot)
    runtimes = {row["id"]: row for row in runtime["projects"]}
    pins = {row["id"]: row for row in candidate["projects"]} if candidate else {}
    selected = {row["id"]: row for row in profile["projects"]} if profile else {}
    rows = []
    for declaration, source in zip(catalog["projects"], inventory["projects"], strict=True):
        name = declaration["id"]
        pin, installed = pins.get(name), runtimes.get(name)
        row = {
            "id": name,
            "layer": declaration["layer"],
            "capabilities": declaration["capabilities"],
            "entrypoints": declaration["entrypoints"],
            "boundaries": declaration["gaps"],
            "declaration_status": "declared",
            "source": source,
            "installation": installed or {"status": "not_checked"},
            "candidate_revision": pin["revision"] if pin else None,
            "source_matches_candidate": source["revision"] == pin["revision"]
            and source["dirty"] is False
            if pin
            else None,
            "profile_matches_candidate": selected.get(name) == pin if pin and profile else None,
            "acceptance": acceptance["status"] if pin else "not_in_candidate",
            "running": {"status": "not_observed"},
        }
        rows.append(row)
    issues = []
    if not inventory["source_inventory_complete"]:
        issues.append("source_inventory_incomplete")
    if runtime["status"] == "blocked":
        issues.append("installation_blocked")
    if acceptance["status"] == "blocked":
        issues.append("acceptance_blocked")
    if active["status"] == "blocked" or active.get("matches_candidate") is False:
        issues.append("active_pointer_mismatch_or_invalid")
    if workflow["status"] == "blocked":
        issues.append("workflow_acceptance_blocked")
    for row in rows:
        if row["source_matches_candidate"] is False or row["profile_matches_candidate"] is False:
            issues.append(f"candidate_identity_mismatch:{row['id']}")
    return {
        "schema_version": SCHEMA,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "catalog_as_of": catalog["as_of"],
        "status": "attention_required" if issues else "observed",
        "issues": issues,
        "projects": rows,
        "acceptance": acceptance,
        "active_release": active,
        "workflow_acceptance": workflow,
        "claims": {
            "source_inventory_complete": inventory["source_inventory_complete"],
            "candidate_integration_evidence_verified": acceptance["status"]
            == "verified_in_combination"
            and "compatibility" in acceptance["case_kinds"],
            "integration_verified": acceptance["status"] == "verified_in_combination"
            and "compatibility" in acceptance["case_kinds"]
            and runtime["status"] == "ready"
            and not issues,
            "acceptance_commands_rerun": False,
            "running_services_verified": False,
            "market_data_certified": False,
        },
    }


def render_status(report: dict) -> str:
    """Standalone escaped read-only view; JSON and HTML share the same result."""

    def escape(value):
        return html.escape(str(value), quote=True)

    rows = []
    for row in report["projects"]:
        source = row["source"]
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{escape(value)}</td>"
                for value in (
                    row["id"],
                    source["source_status"],
                    source["revision"] or "unknown",
                    row["installation"]["status"],
                    row["acceptance"],
                    row["running"]["status"],
                )
            )
            + "</tr>"
        )
    payload = escape(json.dumps(report, indent=2, ensure_ascii=False))
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>量化平台状态</title><style>body{font:15px system-ui;margin:2rem;color:#172536}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd5df;padding:.65rem;text-align:left}"
        "td{overflow-wrap:anywhere}pre{white-space:pre-wrap}header{max-width:75ch}</style>"
        "<header><h1>量化平台状态</h1><p>源码、安装环境、指定组合验收和运行状态分别展示。"
        "未检查、未执行、被阻断均不代表通过；运行服务没有进程证据时保持未观测。</p>"
        f"<p>观测：{escape(report['observed_at'])} · {escape(report['status'])}</p></header>"
        "<table><thead><tr><th>仓库</th><th>源码</th><th>提交</th><th>安装环境</th>"
        "<th>组合验收</th><th>运行服务</th></tr></thead><tbody>"
        + "".join(rows)
        + f"</tbody></table><details><summary>完整证据、能力与边界</summary><pre>{payload}</pre></details></html>\n"
    )
