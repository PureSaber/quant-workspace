"""Complete named-root backups using the existing transfer format and native readers."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

from quant_workspace.capabilities import _git
from quant_workspace.delivery import _canonical, _now, _sha, _state_lock, _write_atomic
from quant_workspace.runtime_identity import hidden_options
from quant_workspace.transfer import (
    SPEC_SCHEMA,
    _file_sha,
    _has_symlink,
    _relative,
    create_transfer_package,
    restore_transfer_package,
    verify_transfer_package,
)

# These probes never execute a strategy, refresh data, or modify frozen bindings.
READER = """
import json, sys
from pathlib import Path
kind, root = sys.argv[1], Path(sys.argv[2])
result = {'reader': kind, 'read_only': True}
if kind in {'paper-account', 'paper-accounts'}:
    from quant_pipeline.research_paper import inspect_account, load_account
    def read_account(path):
        inspection = inspect_account(path)
        if any(a['status'] == 'running' for a in inspection['attempts']):
            raise ValueError('Active or interrupted account attempt blocks backup')
        return {'inspection': inspection, 'bindings': load_account(path)['definition']['recipe']['inputs']}
    if kind == 'paper-account':
        result.update(read_account(root))
    else:
        result['accounts'], result['partial_evidence'] = {}, []
        for directory in sorted(root.iterdir()):
            if directory.is_dir() and directory.name.startswith(('.pending', '.failed')):
                result['partial_evidence'].append({'path': directory.name, 'status': 'preserved_not_activated'})
        for definition in sorted(root.rglob('account.json')):
            relative = definition.parent.relative_to(root).as_posix()
            if any(p.startswith(('.pending', '.failed')) for p in definition.relative_to(root).parts):
                continue
            result['accounts'][relative] = read_account(definition.parent)
        for directory in sorted(root.iterdir()):
            if directory.is_dir() and not directory.name.startswith('.') and not (directory / 'account.json').is_file():
                raise ValueError('Complete account directory has no registration: ' + directory.name)
elif kind == 'projects':
    from quant_studio.projects import ProjectStore
    if not (root / '.projects').is_dir():
        raise ValueError('Project store is absent')
    result['projects'] = [r['id'] for r in ProjectStore(root).list()]
elif kind == 'daily-receipts':
    from quant_pipeline.etf_daily import _read_receipt
    result['receipts'] = [_read_receipt(p)['sha256'] for p in sorted(root.rglob('operation.json'))]
elif kind == 'history':
    from quant_data_kit.research_coverage import load_history
    manifest, frame = load_history(root)
    result['rows'] = len(frame)
elif kind == 'instrument-master':
    from quant_data_kit.instrument_master import load_instrument_master
    manifest, frame = load_instrument_master(root)
    result['rows'] = len(frame)
elif kind == 'inputs':
    from a_share_multifactor.decision_workflow import load_inputs
    manifest, frames = load_inputs(root)
    result['frames'] = sorted(frames)
elif kind == 'json':
    paths = [root] if root.is_file() else sorted(root.rglob('*.json'))
    result['files'] = len([json.loads(p.read_text('utf-8-sig')) for p in paths])
else:
    raise ValueError('Unsupported native reader')
print(json.dumps(result))
"""
READERS = {
    "paper-accounts",
    "paper-account",
    "projects",
    "daily-receipts",
    "history",
    "instrument-master",
    "inputs",
    "json",
}


def _reader_relative(row):
    value = row.get("reader_path", ".")
    relative = Path() if value == "." else _relative(value, "reader_path")
    if relative.anchor:
        raise ValueError("reader_path must be a confined relative path")
    return relative


def _reader_target(row, root):
    root = Path(root).absolute()
    target = root / _reader_relative(row)
    if (
        not target.exists()
        or _has_symlink(root.parent, target)
        or target.resolve() != target.absolute()
        or not target.resolve().is_relative_to(root.resolve())
    ):
        raise ValueError("reader_path is missing, linked or outside its backup root")
    return target


def load_config(path):
    config = json.loads(Path(path).read_text("utf-8-sig"))
    if config.get("schema_version") != "quant.operational-backup/v1":
        raise ValueError("Unsupported operational backup configuration")
    for name in ("source_repo", "python", "state_dir", "archive", "restore_destination"):
        if not Path(config[name]).is_absolute():
            raise ValueError("Backup configuration requires absolute paths")
    categories = set()
    for label, row in config["roots"].items():
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", label):
            raise ValueError("Invalid backup label")
        if not Path(row["path"]).is_absolute() or row.get("reader") not in READERS:
            raise ValueError("Every root needs an absolute path and a supported native reader")
        if row.get("python") and not Path(row["python"]).is_absolute():
            raise ValueError("Reader interpreter must be an absolute path")
        _reader_relative(row)
        required = {"account": {"paper-account", "paper-accounts"}, "project": {"projects"}}.get(
            row["category"]
        )
        if required and row["reader"] not in required:
            raise ValueError("Account and project roots require their native readers")
        categories.add(row["category"])
    if not {"data", "project", "config", "account", "receipt"}.issubset(categories):
        raise ValueError(
            "Complete backup requires data, project, config, account and receipt roots"
        )
    roots = [Path(row["path"]).resolve() for row in config["roots"].values()]
    for key in ("archive", "restore_destination", "state_dir"):
        target = Path(config[key]).resolve()
        if any(
            target == root or target.is_relative_to(root) or root.is_relative_to(target)
            for root in roots
        ):
            raise ValueError(
                "Backup outputs and restore destination must be separate from all source roots"
            )
    return config


def native_read(config, row, path):
    # Resolve against the supplied source or restored root, never the configured
    # original path: restore verification must also work with sources offline.
    target = _reader_target(row, path)
    result = subprocess.run(
        [
            row.get("python", config["python"]),
            "-I",
            "-X",
            "utf8",
            "-c",
            READER,
            row["reader"],
            str(target),
        ],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        timeout=120,
        **hidden_options(),
    )
    if result.returncode:
        raise ValueError("Native restore/backup verification failed: " + result.stderr[-3000:])
    return json.loads(result.stdout)


def _files(config):
    paths = {}
    for label, row in config["roots"].items():
        root = Path(row["path"])
        if not root.exists() or root.is_symlink() or root.resolve() != root.absolute():
            raise ValueError("Backup root is missing or linked: " + label)
        selected = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in selected:
            if _has_symlink(root.parent, path) or path.resolve() != path.absolute():
                raise ValueError("Backup cannot traverse links or junctions")
            if (
                not path.is_file()
                or path.suffix in {".lock", ".tmp"}
                or path.name == "manager.lock"
            ):
                continue
            if (
                (path.parent.name == ".jobs" and path.suffix == ".json")
                or path.name == "operation.json"
                or (row["category"] == "receipt" and path.suffix == ".json")
            ):
                value = json.loads(path.read_text("utf-8-sig"))
                if isinstance(value, dict) and value.get("status", value.get("state")) in {
                    "queued",
                    "running",
                    "cancelling",
                    "registering",
                }:
                    raise ValueError("Active/interrupted work blocks backup")
            relative = path.name if root.is_file() else path.relative_to(root).as_posix()
            paths[label + "/" + relative] = (path, row["category"], _file_sha(path))
    return paths


def _bindings_covered(config, checks):
    roots = [
        Path(row["path"]).resolve() for row in config["roots"].values() if row["category"] == "data"
    ]
    results = list(checks.values())
    results += [
        account for result in checks.values() for account in result.get("accounts", {}).values()
    ]
    for result in results:
        for value in result.get("bindings", {}).values():
            if isinstance(value, str) and Path(value).is_absolute():
                bound = Path(value).resolve()
                if not any(bound == root or bound.is_relative_to(root) for root in roots):
                    raise ValueError("Frozen external input is absent from backup roots: " + value)


def _directories(config):
    return {
        label: ["."] + sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_dir())
        for label, row in config["roots"].items()
        if (root := Path(row["path"])).is_dir()
    }


def preview(config_path):
    config = load_config(config_path)
    files = _files(config)
    checks = {
        label: native_read(config, row, Path(row["path"])) for label, row in config["roots"].items()
    }
    _bindings_covered(config, checks)
    plan = {
        "schema_version": "quant.operational-backup-plan/v1",
        "config_sha256": _file_sha(Path(config_path)),
        "files": {target: value[2] for target, value in files.items()},
        "directories": _directories(config),
        "root_shapes": {
            label: "file" if Path(row["path"]).is_file() else "directory"
            for label, row in config["roots"].items()
        },
        "archive": config["archive"],
        "restore_destination": config["restore_destination"],
        "frozen_bindings_modified": False,
    }
    return {**plan, "plan_sha256": _sha(_canonical(plan))}


def backup(config_path, expected_plan):
    config = load_config(config_path)
    with _state_lock(Path(config["state_dir"])):
        plan = preview(config_path)
        if plan["plan_sha256"] != expected_plan:
            raise ValueError("Backup preview is stale")
        files = _files(config)
        repo = Path(config["source_repo"]).resolve()
        root = Path(os.path.commonpath([str(repo), *(str(v[0]) for v in files.values())]))
        lock = repo / "requirements.lock"
        source = {
            "repo": repo.relative_to(root).as_posix(),
            "revision": _git(repo, "rev-parse", "HEAD"),
            "lock": "requirements.lock",
            "lock_sha256": _file_sha(lock),
        }
        entries = [
            {"source": p.relative_to(root).as_posix(), "target": target, "category": category}
            for target, (p, category, _) in files.items()
        ]
        entries.append(
            {
                "source": lock.relative_to(root).as_posix(),
                "target": "source/requirements.lock",
                "category": "source",
            }
        )
        spec = {
            "schema_version": SPEC_SCHEMA,
            "source": source,
            "files": entries,
            "external_data": [],
            "credentials_required": [],
            "path_mappings": {},
        }
        spec_path = Path(config["state_dir"]) / f"spec-{uuid.uuid4().hex}.json"
        _write_atomic(spec_path, spec)
        result = create_transfer_package(spec_path, root, Path(config["archive"]))
        if {k: v[2] for k, v in _files(config).items()} != plan["files"]:
            raise ValueError("Sources changed during backup; archive has no approved receipt")
        if _directories(config) != plan["directories"]:
            raise ValueError("Source directories changed during backup; preview again")
        receipt = {
            "schema_version": "quant.operational-backup-receipt/v1",
            "created_at": _now(),
            "archive": config["archive"],
            "archive_sha256": result["archive_sha256"],
            "manifest_sha256": result["manifest_sha256"],
            "plan": plan,
            "verified": True,
            "restore_verified": False,
            "scope": "Configured external inputs, projects, configs, accounts and receipts; credentials excluded",
        }
        _write_atomic(Path(config["state_dir"]) / "backup.json", receipt)
        return receipt


def restore_drill(config_path):
    config = load_config(config_path)
    state = Path(config["state_dir"])
    with _state_lock(state):
        receipt = json.loads((state / "backup.json").read_text("utf-8"))
        archive = Path(config["archive"])
        if (
            _file_sha(archive) != receipt["archive_sha256"]
            or _file_sha(Path(config_path)) != receipt["plan"]["config_sha256"]
        ):
            raise ValueError(
                "Backup archive or restore configuration differs from approved receipt"
            )
        manifest = verify_transfer_package(archive)
        destination = Path(config["restore_destination"])
        restore_transfer_package(archive, destination)
        result = {
            "schema_version": "quant.restore-drill/v1",
            "checked_at": _now(),
            "archive_sha256": receipt["archive_sha256"],
            "destination": str(destination),
            "status": "failed",
            "native_readers": {},
            "frozen_bindings_modified": False,
            "production_activated": False,
            "restart_ready": False,
            "scope": "Isolated saved-state validation; original absolute bindings are retained",
        }
        try:
            # Transfer archives contain files. Preserve empty account containers and
            # empty project stores without consulting the possibly offline source.
            for label, directories in receipt["plan"].get("directories", {}).items():
                if (
                    label not in config["roots"]
                    or receipt["plan"]["root_shapes"][label] != "directory"
                ):
                    raise ValueError("Invalid restored directory root")
                for relative in directories:
                    suffix = Path() if relative == "." else _relative(relative, "directory")
                    (destination / label / suffix).mkdir(parents=True, exist_ok=True)
            for label, row in config["roots"].items():
                target = destination / label
                # Use archived shape, never depend on original source availability.
                original = Path(row["path"])
                if receipt["plan"]["root_shapes"][label] == "file":
                    target = target / original.name
                result["native_readers"][label] = native_read(config, row, target)
            # Native readers must not rewrite any restored business bytes.
            for row in manifest["files"]:
                if _file_sha(destination / row["target"]) != row["sha256"]:
                    raise ValueError("Native reader changed restored payload")
            result["status"] = "verified"
        except Exception as exc:
            result["error"] = str(exc)
            raise
        finally:
            _write_atomic(state / "restore.json", result)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("action", choices=("preview", "backup", "verify", "restore-drill"))
    parser.add_argument("--expected-plan-sha256")
    args = parser.parse_args(argv)
    try:
        if args.action == "preview":
            result = preview(args.config)
        elif args.action == "backup":
            result = backup(args.config, args.expected_plan_sha256)
        elif args.action == "verify":
            result = verify_transfer_package(Path(load_config(args.config)["archive"]))
        else:
            result = restore_drill(args.config)
        code = 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        result, code = {"status": "failed", "error": str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
