import json
import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest
from test_transfer import git

from quant_workspace import operational_backup as backup
from quant_workspace.delivery import _write_atomic

NATIVE_READ = backup.native_read


@pytest.fixture
def configured(tmp_path, monkeypatch):
    repo = tmp_path / "source"
    repo.mkdir()
    (repo / "requirements.lock").write_text("packaging==1.0\n")
    git(repo, "init")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture [skip ci]")
    roots = {}
    for name in ("data", "project", "config", "account", "receipt"):
        path = tmp_path / "origin" / name
        path.mkdir(parents=True)
        (path / "record.json").write_text(json.dumps({"name": name}))
        roots[name] = {
            "path": str(path),
            "category": name,
            "reader": {"account": "paper-account", "project": "projects"}.get(name, "json"),
        }
    monkeypatch.setattr(
        backup,
        "native_read",
        lambda config, row, path: (
            NATIVE_READ(config, row, path) if row["reader"] == "json" else {"read_only": True}
        ),
    )
    config = {
        "schema_version": "quant.operational-backup/v1",
        "python": sys.executable,
        "source_repo": str(repo),
        "state_dir": str(tmp_path / "backup-state"),
        "archive": str(tmp_path / "backup.zip"),
        "restore_destination": str(tmp_path / "restored"),
        "roots": roots,
    }
    path = tmp_path / "backup.json"
    _write_atomic(path, config)
    return path, config


def test_backup_restore_uses_transfer_and_never_overwrites_new_financial_state(configured):
    path, config = configured
    jobs = Path(config["roots"]["project"]["path"]) / ".jobs"
    jobs.mkdir()
    (jobs / "completed.json").write_text('{"status":"succeeded"}')
    log = "[2026-10-15] native task finished\nThis is a text log, not a JSON receipt."
    (jobs / "completed.log").write_text(log)
    plan = backup.preview(path)
    receipt = backup.backup(path, plan["plan_sha256"])
    assert receipt["verified"] and not receipt["restore_verified"]
    account = Path(config["roots"]["account"]["path"]) / "record.json"
    account.write_text('{"newer_observation":123}')
    result = backup.restore_drill(path)
    assert result["status"] == "verified"
    assert not result["frozen_bindings_modified"]
    assert not result["restart_ready"] and not result["production_activated"]
    assert account.read_text() == '{"newer_observation":123}'
    assert json.loads(
        (Path(config["restore_destination"]) / "account/record.json").read_text()
    ) == {"name": "account"}
    assert (Path(config["restore_destination"]) / "project/.jobs/completed.log").read_text() == log
    with pytest.raises(ValueError, match="must not exist"):
        backup.restore_drill(path)


def test_stale_backup_and_damaged_archive_rejected_without_restore(configured):
    path, config = configured
    plan = backup.preview(path)
    (Path(config["roots"]["data"]["path"]) / "record.json").write_text('{"new":1}')
    with pytest.raises(ValueError, match="stale"):
        backup.backup(path, plan["plan_sha256"])
    backup.backup(path, backup.preview(path)["plan_sha256"])
    with ZipFile(config["archive"], "a") as archive:
        archive.writestr("payload/unlisted.txt", "damage")
    with pytest.raises(ValueError, match="differs"):
        backup.restore_drill(path)
    assert not Path(config["restore_destination"]).exists()


def test_empty_account_container_restores_with_original_source_offline(configured, monkeypatch):
    pytest.importorskip("quant_pipeline")
    path, config = configured
    account = Path(config["roots"]["account"]["path"])
    (account / "record.json").unlink()
    config["roots"]["account"]["reader"] = "paper-accounts"
    empty = Path(config["roots"]["config"]["path"]) / "empty/nested"
    empty.mkdir(parents=True)
    monkeypatch.setattr(
        backup,
        "native_read",
        lambda config, row, path: (
            NATIVE_READ(config, row, path)
            if row["reader"] in {"json", "paper-accounts"}
            else {"read_only": True}
        ),
    )
    _write_atomic(path, config)
    backup.backup(path, backup.preview(path)["plan_sha256"])
    account.rename(account.with_name("offline-account"))
    restored = backup.restore_drill(path)
    assert restored["status"] == "verified"
    assert restored["native_readers"]["account"]["accounts"] == {}
    assert (Path(config["restore_destination"]) / "config/empty/nested").is_dir()
    assert not account.exists()


def test_backup_rejects_incomplete_scope_busy_jobs_and_uncovered_bindings(configured):
    path, config = configured
    del config["roots"]["receipt"]
    _write_atomic(path, config)
    with pytest.raises(ValueError, match="Complete backup"):
        backup.preview(path)
    config["roots"]["receipt"] = {**config["roots"]["config"], "category": "receipt"}
    _write_atomic(path, config)
    _write_atomic(Path(config["roots"]["project"]["path"]) / ".jobs/job.json", {"status": "queued"})
    with pytest.raises(ValueError, match="Active"):
        backup.preview(path)
    with pytest.raises(ValueError, match="absent"):
        backup._bindings_covered(
            config, {"account": {"bindings": {"bundle": str(path.parent / "missing")}}}
        )


def test_occupied_destination_and_native_reader_failure_are_recorded(configured, monkeypatch):
    path, config = configured
    backup.backup(path, backup.preview(path)["plan_sha256"])
    destination = Path(config["restore_destination"])
    destination.mkdir()
    with pytest.raises(ValueError, match="must not exist"):
        backup.restore_drill(path)
    destination.rmdir()
    monkeypatch.setattr(
        backup, "native_read", lambda *_: (_ for _ in ()).throw(ValueError("damaged native ledger"))
    )
    with pytest.raises(ValueError, match="native ledger"):
        backup.restore_drill(path)
    saved = json.loads((Path(config["state_dir"]) / "restore.json").read_text())
    assert saved["status"] == "failed" and "native ledger" in saved["error"]


def test_original_configuration_and_external_inputs_are_byte_preserved(configured):
    path, config = configured
    frozen = Path(config["roots"]["config"]["path"]) / "record.json"
    frozen.write_text(json.dumps({"bundle": config["roots"]["data"]["path"]}))
    before = frozen.read_bytes()
    backup.backup(path, backup.preview(path)["plan_sha256"])
    backup.restore_drill(path)
    restored = Path(config["restore_destination"]) / "config/record.json"
    assert frozen.read_bytes() == restored.read_bytes() == before
    mapping = json.loads(
        (Path(config["restore_destination"]) / "migration-paths.generated.json").read_text()
    )
    assert mapping["mappings"] == {}


@pytest.mark.parametrize(
    "reader_path",
    [
        "/outside",
        "C:/outside",
        "C:outside",
        "//host/share",
        "..",
        "a/../b",
        "../b",
        "a\\b",
        "",
        None,
        3,
        "a//b",
        "./runs",
        "runs/",
    ],
)
def test_reader_path_configuration_rejects_noncanonical_or_escaping_paths(configured, reader_path):
    path, config = configured
    config["roots"]["data"]["reader_path"] = reader_path
    _write_atomic(path, config)
    with pytest.raises(ValueError, match="reader_path"):
        backup.load_config(path)


def test_reader_subtree_roundtrip_archives_full_root_and_preserves_binding_scope(configured):
    path, config = configured
    row = config["roots"]["data"]
    root = Path(row["path"])
    row["reader_path"] = "etf/snapshots/frozen"
    selected = root / row["reader_path"]
    selected.mkdir(parents=True)
    (selected / "input.json").write_text('{"frozen":true}')
    outside_reader = root / "other.json"
    outside_reader.write_text("preserve these bytes without JSON interpretation")
    _write_atomic(path, config)
    # This input is outside reader_path but still covered by the complete data root.
    backup._bindings_covered(config, {"account": {"bindings": {"catalog": str(outside_reader)}}})
    with pytest.raises(ValueError, match="absent"):
        backup._bindings_covered(config, {"account": {"bindings": {"catalog": str(path)}}})
    plan = backup.preview(path)
    assert "data/other.json" in plan["files"]
    assert "data/etf/snapshots/frozen/input.json" in plan["files"]
    backup.backup(path, plan["plan_sha256"])
    root.rename(root.with_name("offline-data"))
    result = backup.restore_drill(path)
    assert result["status"] == "verified"
    assert result["native_readers"]["data"]["files"] == 1
    restored = Path(config["restore_destination"]) / "data"
    assert (
        restored / "other.json"
    ).read_text() == "preserve these bytes without JSON interpretation"
    assert (restored / row["reader_path"] / "input.json").read_text() == '{"frozen":true}'
    assert not root.exists()


def test_reader_path_does_not_allow_duplicate_source_roots(configured):
    path, config = configured
    root = Path(config["roots"]["data"]["path"])
    child = root / "snapshot"
    child.mkdir()
    (child / "input.json").write_text("{}")
    config["roots"]["data"]["reader_path"] = "snapshot"
    config["roots"]["duplicate"] = {"path": str(child), "reader": "json", "category": "data"}
    _write_atomic(path, config)
    with pytest.raises(ValueError, match="must be unique"):
        backup.backup(path, backup.preview(path)["plan_sha256"])
    assert not (Path(config["state_dir"]) / "backup.json").exists()


def test_reader_path_default_dot_and_file_targets_are_supported(tmp_path):
    target = tmp_path / "record.json"
    target.write_text("{}")
    config = {"python": sys.executable}
    for row, root in (
        ({"reader": "json"}, target),
        ({"reader": "json", "reader_path": "."}, target),
        ({"reader": "json", "reader_path": "record.json"}, tmp_path),
    ):
        assert NATIVE_READ(config, row, root)["files"] == 1
    with pytest.raises(ValueError, match="reader_path.*missing"):
        NATIVE_READ(config, {"reader": "json", "reader_path": "missing"}, target)


def test_reader_path_missing_source_and_restored_target_fail_closed(configured, monkeypatch):
    path, config = configured
    config["roots"]["data"]["reader_path"] = "missing.json"
    _write_atomic(path, config)
    with pytest.raises(ValueError, match="reader_path.*missing"):
        backup.preview(path)
    assert not Path(config["archive"]).exists()
    config["roots"]["data"]["reader_path"] = "record.json"
    _write_atomic(path, config)
    backup.backup(path, backup.preview(path)["plan_sha256"])
    restore = backup.restore_transfer_package

    def remove_restored_reader(archive, destination):
        result = restore(archive, destination)
        (destination / "data/record.json").unlink()
        return result

    monkeypatch.setattr(backup, "restore_transfer_package", remove_restored_reader)
    with pytest.raises(ValueError, match="reader_path.*missing"):
        backup.restore_drill(path)
    receipt = json.loads((Path(config["state_dir"]) / "restore.json").read_text())
    assert receipt["status"] == "failed" and "reader_path" in receipt["error"]


def directory_link(link, target):
    if os.name == "nt":
        # Junctions exercise actual Windows links without requiring symlink privileges.
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"

        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"New-Item -ItemType Junction -Path {quote(link)} -Target {quote(target)} | Out-Null",
            ],
            check=True,
            capture_output=True,
            **backup.hidden_options(),
        )
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("nested", [False, True])
def test_reader_path_rejects_source_links_including_in_root_links(configured, nested):
    path, config = configured
    row = config["roots"]["data"]
    root = Path(row["path"])
    target = root / "real"
    target.mkdir()
    (target / "record.json").write_text("{}")
    link = root / "linked"
    directory_link(link, target)
    row["reader_path"] = "linked/record.json" if nested else "linked"
    _write_atomic(path, config)
    try:
        with pytest.raises(ValueError, match="reader_path.*linked"):
            NATIVE_READ(config, row, root)
        with pytest.raises(ValueError, match="links or junctions"):
            backup.preview(path)
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


def test_reader_path_rejects_restored_junction_before_native_read(configured, monkeypatch):
    path, config = configured
    row = config["roots"]["data"]
    root = Path(row["path"])
    selected = root / "selected"
    selected.mkdir()
    (selected / "record.json").write_text("{}")
    row["reader_path"] = "selected"
    _write_atomic(path, config)
    backup.backup(path, backup.preview(path)["plan_sha256"])
    restore = backup.restore_transfer_package
    outside = path.parent / "outside"
    outside.mkdir()
    (outside / "record.json").write_text('{"preserved":true}')
    link = Path(config["restore_destination"]) / "data/selected"

    def redirect_restored_reader(archive, destination):
        result = restore(archive, destination)
        (link / "record.json").unlink()
        link.rmdir()
        directory_link(link, outside)
        return result

    monkeypatch.setattr(backup, "restore_transfer_package", redirect_restored_reader)
    try:
        with pytest.raises(ValueError, match="reader_path.*linked"):
            backup.restore_drill(path)
        receipt = json.loads((Path(config["state_dir"]) / "restore.json").read_text())
        assert receipt["status"] == "failed" and "data" not in receipt["native_readers"]
        assert "reader_path" in receipt["error"]
        assert (outside / "record.json").read_text() == '{"preserved":true}'
    finally:
        if link.exists():
            link.rmdir() if os.name == "nt" else link.unlink()


def test_real_native_account_project_input_and_receipt_restore(configured, monkeypatch):
    pytest.importorskip("quant_studio")
    pytest.importorskip("quant_pipeline")
    from datetime import datetime, timezone

    import quant_report_hub.research_workbench
    from quant_lab.research import candidates, digest, load_recipe
    from quant_pipeline import research_paper as paper
    from quant_pipeline.research_demo import create_demo
    from quant_studio.projects import ProjectStore

    path, config = configured
    monkeypatch.setattr(backup, "native_read", NATIVE_READ)
    base = path.parent
    recipe = load_recipe(create_demo(base / "demo"))
    recipe["interval"] = {"start": "2023-04-03", "end": "2023-05-30"}
    recipe["diagnostics"] = {}
    summary = {
        "recipe": recipe,
        "definition_sha256": "test-study",
        "results": [
            {
                "candidate": candidates(recipe)[0],
                "status": "completed",
                "identity": {"code": "fixture"},
                "data_identity": paper.input_identity(recipe),
                "scope": "synthetic-software-demonstration",
                "metrics": {"total_return": 0.1},
            }
        ],
    }
    monkeypatch.setattr(quant_report_hub.research_workbench, "load_study", lambda _: summary)
    monkeypatch.setattr(paper, "equity_code_identity", lambda: {"code": "fixture"})
    study = base / "study.json"
    study.write_text("fixture")
    account = base / "native-account"
    paper.promote(
        study,
        "base",
        account,
        account_id="backup-fixture",
        start="2023-06-01",
        end="2023-06-05",
        now=datetime(2023, 5, 31, tzinfo=timezone.utc),
    )
    runs = base / "native-state/runs"
    ProjectStore(runs).save("验证项目", "仅使用合成测试资料")
    (runs.parent / "state.json").write_text('{"preserved":true}')
    config["roots"]["account"] = {
        "path": str(account),
        "category": "account",
        "reader": "paper-account",
    }
    config["roots"]["project"] = {
        "path": str(runs.parent),
        "category": "project",
        "reader": "projects",
        "reader_path": "runs",
    }
    config["roots"]["data"] = {
        "path": str(Path(recipe["inputs"]["bundle"]).parent),
        "category": "data",
        "reader": "inputs",
        "reader_path": Path(recipe["inputs"]["bundle"]).name,
    }
    receipt_root = Path(config["roots"]["receipt"]["path"])
    operation = {"status": "succeeded", "started_at": "2023-06-01T00:00:00Z"}
    operation["sha256"] = digest(operation)
    _write_atomic(receipt_root / "session/operation.json", operation)
    config["roots"]["receipt"]["reader"] = "daily-receipts"
    _write_atomic(path, config)
    backup.backup(path, backup.preview(path)["plan_sha256"])
    definition = (account / "account.json").read_bytes()
    Path(recipe["inputs"]["bundle"]).rename(base / "offline-inputs")
    result = backup.restore_drill(path)
    checks = result["native_readers"]
    assert checks["account"]["inspection"]["account_id"] == "backup-fixture"
    assert checks["account"]["bindings"] == recipe["inputs"]
    assert checks["project"]["projects"]
    assert checks["data"]["frames"]
    assert checks["receipt"]["receipts"] == [operation["sha256"]]
    assert (
        Path(config["restore_destination"]) / "project/state.json"
    ).read_text() == '{"preserved":true}'
    assert (account / "account.json").read_bytes() == definition
    assert (Path(config["restore_destination"]) / "account/account.json").read_bytes() == definition
    # A configured account container discovers later registrations automatically.
    import shutil

    (base / "offline-inputs").rename(Path(recipe["inputs"]["bundle"]))
    accounts = base / "all-accounts"
    shutil.copytree(account, accounts / "sim-one")
    (accounts / ".failed-native-attempt").mkdir()
    (accounts / ".failed-native-attempt/diagnostic.txt").write_text("preserve partial evidence")
    config["roots"]["account"].update(path=str(accounts), reader="paper-accounts")
    config.update(
        state_dir=str(base / "multi-state"),
        archive=str(base / "multi.zip"),
        restore_destination=str(base / "multi-restore"),
    )
    _write_atomic(path, config)
    old_plan = backup.preview(path)
    paper.promote(
        study,
        "base",
        accounts / "sim-two",
        account_id="backup-fixture-two",
        start="2023-06-01",
        end="2023-06-05",
        now=datetime(2023, 5, 31, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError, match="stale"):
        backup.backup(path, old_plan["plan_sha256"])
    backup.backup(path, backup.preview(path)["plan_sha256"])
    multi = backup.restore_drill(path)["native_readers"]["account"]
    assert set(multi["accounts"]) == {"sim-one", "sim-two"}
    assert multi["partial_evidence"] == [
        {"path": ".failed-native-attempt", "status": "preserved_not_activated"}
    ]
    assert (
        Path(config["restore_destination"]) / "account/.failed-native-attempt/diagnostic.txt"
    ).read_text() == "preserve partial evidence"
