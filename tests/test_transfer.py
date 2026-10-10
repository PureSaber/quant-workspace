from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from quant_workspace import transfer
from quant_workspace.cli import main


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def migration_fixture(tmp_path: Path) -> dict:
    root = tmp_path / "origin"
    repo = root / "app"
    repo.mkdir(parents=True)
    (repo / "requirements.lock").write_text("packaging==1.0\n", encoding="utf-8")
    (repo / "research.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "root = Path(__file__).resolve().parents[1]\n"
        "data = json.loads((root / 'data/input.json').read_text())\n"
        "cfg = json.loads((root / 'config/settings.json').read_text())\n"
        "print(json.dumps({'result': sum(data['values']) * cfg['scale']}, sort_keys=True))\n",
        encoding="utf-8",
    )
    (root / "data").mkdir()
    (root / "data/input.json").write_text('{"values":[1,2,3]}\n', encoding="utf-8")
    (root / "config").mkdir()
    (root / "config/settings.json").write_text('{"scale":2}\n', encoding="utf-8")
    (root / "runs").mkdir()
    (root / "runs/result.json").write_text('{"result":12}\n', encoding="utf-8")
    git(repo, "init")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    spec = {
        "schema_version": transfer.SPEC_SCHEMA,
        "source": {
            "repo": "app",
            "revision": git(repo, "rev-parse", "HEAD"),
            "lock": "requirements.lock",
            "lock_sha256": digest(repo / "requirements.lock"),
        },
        "files": [
            {
                "category": "source",
                "source": "app/requirements.lock",
                "target": "app/requirements.lock",
            },
            {"category": "source", "source": "app/research.py", "target": "app/research.py"},
            {"category": "data", "source": "data/input.json", "target": "data/input.json"},
            {
                "category": "config",
                "source": "config/settings.json",
                "target": "config/settings.json",
            },
            {"category": "run", "source": "runs/result.json", "target": "runs/result.json"},
        ],
        "external_data": [{"id": "market-history", "description": "未打包的授权行情"}],
        "credentials_required": ["MARKET_API_TOKEN"],
        "path_mappings": {
            "research_config": "config/settings.json",
            "restored_runs": "runs/result.json",
        },
    }
    spec_path = tmp_path / "transfer-spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return {"root": root, "repo": repo, "spec": spec, "spec_path": spec_path}


def test_transfer_restore_hashes_and_synthetic_reproduction(migration_fixture, tmp_path, capsys):
    archive = tmp_path / "migration.zip"
    manifest = transfer.create_transfer_package(
        migration_fixture["spec_path"], migration_fixture["root"], archive
    )
    assert manifest["claims"]["credentials_packaged"] is False
    assert main(["verify-transfer", "--archive", str(archive)]) == 0
    assert json.loads(capsys.readouterr().out)["manifest_sha256"] == manifest["manifest_sha256"]
    destination = tmp_path / "restored"
    status = transfer.restore_transfer_package(archive, destination)
    assert status["integrity_status"] == "verified"
    assert status["status"] == "needs_configuration"
    assert status["research_reproduction_status"] == "not_run"
    for row in manifest["files"]:
        assert digest(destination / row["target"]) == row["sha256"]
    completed = subprocess.run(
        [sys.executable, str(destination / "app/research.py")],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == {"result": 12}
    generated = json.loads((destination / "migration-paths.generated.json").read_text())
    assert generated["historical_files_modified"] is False
    assert generated["mappings"]["research_config"] == str(
        (destination / "config/settings.json").resolve()
    )


@pytest.mark.parametrize(
    "target",
    [
        "MIGRATION_STATUS.json",
        "migration_status.JSON",
        "migration-paths.generated.json",
        "migration-paths.generated.json/child",
        "MIGRATION_STATUS.json.",
        "MIGRATION_STATUS.json ",
        "CON.json",
    ],
)
def test_transfer_reserves_generated_paths_and_rejects_windows_aliases(
    migration_fixture, tmp_path, target
):
    spec = migration_fixture["spec"]
    spec["files"][-1]["target"] = target
    spec["path_mappings"]["restored_runs"] = target
    migration_fixture["spec_path"].write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError, match="reserved|canonical"):
        transfer.create_transfer_package(
            migration_fixture["spec_path"], migration_fixture["root"], tmp_path / "bad.zip"
        )
    assert not (tmp_path / "bad.zip").exists()


def test_transfer_rejects_case_insensitive_target_collision(migration_fixture, tmp_path):
    spec = migration_fixture["spec"]
    spec["files"][-1]["target"] = "DATA/INPUT.JSON"
    spec["path_mappings"]["restored_runs"] = "DATA/INPUT.JSON"
    migration_fixture["spec_path"].write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        transfer.create_transfer_package(
            migration_fixture["spec_path"], migration_fixture["root"], tmp_path / "bad.zip"
        )


@pytest.mark.parametrize(
    "filename,content",
    [
        (".env.local", "OPENAI_API_KEY=fixture-only-never-a-live-key\n"),
        (".ENV.production", "ordinary=fixture\n"),
        ("settings.txt", "export OPENAI_API_KEY=fixture-only-never-a-live-key\n"),
        ("provider.json", '{"AWS_SECRET_ACCESS_KEY":"fixture-only"}'),
        ("headers.txt", "Authorization: Bearer fixture-only\n"),
        ("settings.yaml", "database: postgres://fixture:fixture-only@localhost/db\n"),
    ],
)
def test_transfer_rejects_prefixed_credentials_and_dotenv_variants(
    migration_fixture, tmp_path, filename, content
):
    source = "config/" + filename
    (migration_fixture["root"] / source).write_text(content, encoding="utf-8")
    spec = migration_fixture["spec"]
    spec["files"].append({"category": "config", "source": source, "target": source})
    migration_fixture["spec_path"].write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError, match="ensitive|credential|private"):
        transfer.create_transfer_package(
            migration_fixture["spec_path"], migration_fixture["root"], tmp_path / "secret.zip"
        )
    assert not (tmp_path / "secret.zip").exists()


@pytest.mark.parametrize(
    "content",
    [
        "OPENAI_API_KEY=${OPENAI_API_KEY}\n",
        'export OPENAI_API_KEY="${OPENAI_API_KEY}"\n',
        '{"api_key": "{{ API_KEY }}"}',
    ],
)
def test_transfer_allows_environment_placeholders(migration_fixture, tmp_path, content):
    (migration_fixture["root"] / "config/settings.json").write_text(content, encoding="utf-8")
    archive = tmp_path / "placeholders.zip"
    transfer.create_transfer_package(
        migration_fixture["spec_path"], migration_fixture["root"], archive
    )
    transfer.verify_transfer_package(archive)


@pytest.mark.parametrize(
    "target,content",
    [
        ("MIGRATION_STATUS.json", b"fixture"),
        ("config/.env.local", b"fixture"),
        ("config/provider.txt", b"OPENAI_API_KEY=fixture-only-never-a-live-key\n"),
    ],
)
def test_transfer_rejects_independently_built_unsafe_archive(
    migration_fixture, tmp_path, target, content
):
    archive = tmp_path / "original.zip"
    transfer.create_transfer_package(
        migration_fixture["spec_path"], migration_fixture["root"], archive
    )
    manifest, payload = transfer._read_archive(archive)
    original = manifest["files"][-1]["target"]
    manifest["files"][-1].update(
        target=target, size=len(content), sha256=hashlib.sha256(content).hexdigest()
    )
    manifest["path_mappings"]["restored_runs"] = target
    spec = dict(migration_fixture["spec"])
    spec["files"] = [
        {key: row[key] for key in ("category", "source", "target")} for row in manifest["files"]
    ]
    spec["path_mappings"] = manifest["path_mappings"]
    manifest["spec_sha256"] = hashlib.sha256(transfer._canonical(spec)).hexdigest()
    manifest["manifest_sha256"] = transfer._manifest_hash(manifest)
    payload.pop(original)
    payload[target] = content
    unsafe = tmp_path / "independently-built.zip"
    with zipfile.ZipFile(unsafe, "w") as stream:
        stream.writestr(transfer.MANIFEST_NAME, transfer._canonical(manifest))
        for name, raw in payload.items():
            stream.writestr("payload/" + name, raw)
    with pytest.raises(ValueError, match="reserved|ensitive|credential"):
        transfer.verify_transfer_package(unsafe)
    destination = tmp_path / "unsafe-restore"
    with pytest.raises(ValueError, match="reserved|ensitive|credential"):
        transfer.restore_transfer_package(unsafe, destination)
    assert not destination.exists()


def test_transfer_rejects_dirty_or_changed_source(migration_fixture, tmp_path, monkeypatch):
    (migration_fixture["repo"] / "dirty.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(ValueError, match="dirty"):
        transfer.create_transfer_package(
            migration_fixture["spec_path"], migration_fixture["root"], tmp_path / "dirty.zip"
        )
    (migration_fixture["repo"] / "dirty.txt").unlink()

    original = transfer._verify_source
    calls = 0

    def mutate_on_second_check(spec, root):
        nonlocal calls
        calls += 1
        if calls == 2:
            (migration_fixture["repo"] / "research.py").write_text("changed\n", encoding="utf-8")
        return original(spec, root)

    monkeypatch.setattr(transfer, "_verify_source", mutate_on_second_check)
    with pytest.raises(ValueError, match="dirty"):
        transfer.create_transfer_package(
            migration_fixture["spec_path"], migration_fixture["root"], tmp_path / "changed.zip"
        )


def test_transfer_rejects_archive_tampering_traversal_links_and_overwrite(
    migration_fixture, tmp_path
):
    archive = tmp_path / "migration.zip"
    transfer.create_transfer_package(
        migration_fixture["spec_path"], migration_fixture["root"], archive
    )
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            raw = source.read(info.filename)
            if info.filename == "payload/data/input.json":
                raw = b'{"values":[999]}\n'
            target.writestr(info.filename, raw)
    with pytest.raises(ValueError, match="digest mismatch"):
        transfer.verify_transfer_package(tampered)

    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(traversal, "w") as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info.filename))
        target.writestr("payload/../escape", b"escape")
    with pytest.raises(ValueError, match="canonical relative"):
        transfer.verify_transfer_package(traversal)

    linked = tmp_path / "link.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(linked, "w") as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info.filename))
        info = zipfile.ZipInfo("payload/link")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        target.writestr(info, "target")
    with pytest.raises(ValueError, match="regular files"):
        transfer.verify_transfer_package(linked)

    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(ValueError, match="must not exist"):
        transfer.restore_transfer_package(archive, destination)


def test_transfer_rejects_private_key_and_requires_explicit_lock(migration_fixture, tmp_path):
    secret = migration_fixture["root"] / "config/token.txt"
    secret.write_text('{"api_key": "live-secret"}\n', encoding="utf-8")
    spec = migration_fixture["spec"]
    spec["files"].append(
        {"category": "config", "source": "config/token.txt", "target": "config/token.txt"}
    )
    secret_spec = tmp_path / "secret-spec.json"
    secret_spec.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError, match="Credential"):
        transfer.create_transfer_package(
            secret_spec, migration_fixture["root"], tmp_path / "secret.zip"
        )

    spec["files"] = [row for row in spec["files"] if row["source"] != "app/requirements.lock"]
    no_lock = tmp_path / "no-lock.json"
    no_lock.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError, match="lock must be explicitly packaged"):
        transfer.create_transfer_package(
            no_lock, migration_fixture["root"], tmp_path / "no-lock.zip"
        )
