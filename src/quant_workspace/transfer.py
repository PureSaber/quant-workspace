"""Allowlisted, credential-free migration archives with safe fresh-directory restore."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path

from quant_workspace.capabilities import _git
from quant_workspace.runtime_readiness import lock_requirements

SPEC_SCHEMA = "quant.transfer-spec/v1"
MANIFEST_SCHEMA = "quant.transfer-manifest/v1"
STATUS_SCHEMA = "quant.transfer-restore-status/v1"
MANIFEST_NAME = "TRANSFER_MANIFEST.json"
_CATEGORIES = {"run", "data", "config", "runtime", "source"}
_SENSITIVE_PARTS = {
    ".env",
    ".ssh",
    "credential",
    "credentials",
    "secret",
    "secrets",
    "private",
    "id_rsa",
    "id_ed25519",
}
_SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kdbx"}
_SENSITIVE_CONTENT = re.compile(
    rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    rb"(?:^|[{,])\s*['\"]?(?:password|passwd|api[_-]?key|access[_-]?token|secret[_-]?key|"
    rb"client[_-]?secret)['\"]?\s*[:=]\s*(?!(?:['\"]?\$\{|['\"]?\{\{))\S[^\r\n]*\r?$",
    re.IGNORECASE | re.MULTILINE,
)
_MAX_FILE = 1_000_000_000
_MAX_TOTAL = 4_000_000_000


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    before = path.stat()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    after = path.stat()
    before_identity = (before.st_size, before.st_mtime_ns, getattr(before, "st_ino", 0))
    after_identity = (after.st_size, after.st_mtime_ns, getattr(after, "st_ino", 0))
    if before_identity != after_identity:
        raise ValueError("Archive changed while hashing")
    return digest.hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _relative(value: str, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError(f"{label} must be a canonical relative path")
    path = Path(value)
    if path.is_absolute() or path == Path(".") or ".." in path.parts or path.as_posix() != value:
        raise ValueError(f"{label} must be a canonical relative path")
    return path


def _inside(root: Path, value: str, label: str) -> Path:
    root = root.resolve()
    path = root / _relative(value, label)
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"{label} escapes its root")
    return path


def _load_json(path: Path, maximum: int = 4_000_000) -> tuple[dict, bytes]:
    raw = Path(path).read_bytes()
    if len(raw) > maximum:
        raise ValueError("Transfer JSON is too large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError("Transfer JSON must be an object")
    return value, raw


def _has_symlink(root: Path, path: Path) -> bool:
    root = root.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return True
    return False


def _sensitive_path(path: Path) -> bool:
    return any(part.casefold() in _SENSITIVE_PARTS for part in path.parts) or path.suffix.casefold() in _SENSITIVE_SUFFIXES


def _validate_spec(spec: dict) -> None:
    fields = {
        "schema_version",
        "source",
        "files",
        "external_data",
        "credentials_required",
        "path_mappings",
    }
    if set(spec) != fields or spec.get("schema_version") != SPEC_SCHEMA:
        raise ValueError("Invalid transfer spec fields")
    source = spec["source"]
    if not isinstance(source, dict) or set(source) != {"repo", "revision", "lock", "lock_sha256"}:
        raise ValueError("Invalid transfer source pin")
    _relative(source["repo"], "source repo")
    _relative(source["lock"], "source lock")
    for key, length in (("revision", 40), ("lock_sha256", 64)):
        value = source[key]
        if not isinstance(value, str) or len(value) != length or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ValueError("Transfer source requires exact Git and lock digests")
    files = spec["files"]
    if not isinstance(files, list) or not files:
        raise ValueError("Transfer spec requires an explicit file allowlist")
    sources: set[str] = set()
    targets: set[str] = set()
    lock_source = (Path(source["repo"]) / source["lock"]).as_posix()
    lock_included = False
    for item in files:
        if not isinstance(item, dict) or set(item) != {"category", "source", "target"}:
            raise ValueError("Invalid transfer file entry")
        if item["category"] not in _CATEGORIES:
            raise ValueError("Unsupported transfer file category")
        src = _relative(item["source"], "allowlist source").as_posix()
        target = _relative(item["target"], "allowlist target").as_posix()
        if src in sources or target in targets:
            raise ValueError("Transfer source and target paths must be unique")
        if _sensitive_path(Path(src)) or _sensitive_path(Path(target)):
            raise ValueError("Sensitive/private path is forbidden in transfer archives")
        sources.add(src)
        targets.add(target)
        lock_included = lock_included or (src == lock_source and item["category"] == "source")
    if not lock_included:
        raise ValueError("The pinned source lock must be explicitly packaged as source")
    if not isinstance(spec["external_data"], list):
        raise TypeError("external_data must be an array")
    external_ids: set[str] = set()
    for item in spec["external_data"]:
        if not isinstance(item, dict) or set(item) != {"id", "description"}:
            raise ValueError("Invalid external data declaration")
        if not all(isinstance(item[key], str) and item[key] for key in item):
            raise ValueError("External data declarations require id and description")
        if item["id"] in external_ids:
            raise ValueError("Duplicate external data declaration")
        external_ids.add(item["id"])
    credentials = spec["credentials_required"]
    if not isinstance(credentials, list) or not all(
        isinstance(item, str) and item for item in credentials
    ) or len(credentials) != len(set(credentials)):
        raise ValueError("credentials_required must contain unique identifiers only")
    mappings = spec["path_mappings"]
    if not isinstance(mappings, dict) or not all(
        isinstance(key, str)
        and key
        and isinstance(value, str)
        and _relative(value, "path mapping target")
        for key, value in mappings.items()
    ):
        raise ValueError("Path mappings must map labels to restored relative paths")
    if not set(mappings.values()).issubset(targets):
        raise ValueError("Path mappings may reference only packaged targets")


def _verify_source(spec: dict, root: Path) -> Path:
    source = spec["source"]
    repo = _inside(root, source["repo"], "source repo")
    if Path(_git(repo, "rev-parse", "--show-toplevel")).resolve() != repo.resolve():
        raise ValueError("Transfer source is not an independent Git checkout")
    if _git(repo, "rev-parse", "HEAD") != source["revision"]:
        raise ValueError("Transfer source revision mismatch")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("Transfer source checkout is dirty")
    lock = _inside(repo, source["lock"], "source lock")
    lock_raw = lock.read_bytes()
    if _sha(lock_raw) != source["lock_sha256"]:
        raise ValueError("Transfer source lock mismatch")
    lock_requirements(lock_raw.decode("utf-8"), repo=repo)
    return repo


def create_transfer_package(spec_path: Path, source_root: Path, out: Path) -> dict:
    """Create a ZIP containing only exact allowlisted files and a hashed manifest."""
    spec, _ = _load_json(Path(spec_path))
    _validate_spec(spec)
    source_root = Path(source_root)
    if source_root.is_symlink():
        raise ValueError("Transfer source root may not be a symlink")
    source_root = source_root.resolve()
    _verify_source(spec, source_root)
    out = Path(out).resolve()
    if out.exists() or out.is_symlink():
        raise ValueError("Transfer archive already exists")
    rows = []
    contents: dict[str, bytes] = {}
    total = 0
    snapshots: dict[str, tuple[int, int, int]] = {}
    for item in spec["files"]:
        source = _inside(source_root, item["source"], "allowlist source")
        if _has_symlink(source_root, source) or not source.is_file():
            raise ValueError(f"Allowlisted source must be a regular non-link file: {item['source']}")
        before = source.stat()
        raw = source.read_bytes()
        after = source.stat()
        identity = (before.st_size, before.st_mtime_ns, getattr(before, "st_ino", 0))
        if identity != (after.st_size, after.st_mtime_ns, getattr(after, "st_ino", 0)):
            raise ValueError(f"Source changed while reading: {item['source']}")
        if len(raw) > _MAX_FILE or total + len(raw) > _MAX_TOTAL:
            raise ValueError("Transfer payload exceeds size limits")
        if _SENSITIVE_CONTENT.search(raw):
            raise ValueError(f"Credential/private-key content is forbidden: {item['source']}")
        total += len(raw)
        snapshots[item["source"]] = identity
        contents[item["target"]] = raw
        rows.append(
            {
                "category": item["category"],
                "source": item["source"],
                "target": item["target"],
                "size": len(raw),
                "sha256": _sha(raw),
            }
        )
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "manifest_sha256": "",
        "spec_sha256": _sha(_canonical(spec)),
        "source": spec["source"],
        "files": rows,
        "external_data": spec["external_data"],
        "credentials_required": spec["credentials_required"],
        "path_mappings": spec["path_mappings"],
        "claims": {
            "credentials_packaged": False,
            "external_data_complete": not bool(spec["external_data"]),
            "research_reproduced": False,
        },
    }
    manifest["manifest_sha256"] = _manifest_hash(manifest)
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(f".{out.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as raw_stream:
            with zipfile.ZipFile(raw_stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(MANIFEST_NAME, _canonical(manifest))
                for target in sorted(contents):
                    archive.writestr(f"payload/{target}", contents[target])
            raw_stream.flush()
            os.fsync(raw_stream.fileno())
        for item in spec["files"]:
            source = _inside(source_root, item["source"], "allowlist source")
            current = source.stat()
            identity = (current.st_size, current.st_mtime_ns, getattr(current, "st_ino", 0))
            if identity != snapshots[item["source"]] or _sha(source.read_bytes()) != next(
                row["sha256"] for row in rows if row["source"] == item["source"]
            ):
                raise ValueError(f"Source changed while packaging: {item['source']}")
        _verify_source(spec, source_root)
        temporary.replace(out)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {**manifest, "archive": str(out), "archive_sha256": _file_sha(out)}


def _manifest_hash(manifest: dict) -> str:
    payload = dict(manifest)
    payload.pop("manifest_sha256", None)
    return _sha(_canonical(payload))


def _zip_is_link(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _read_archive(archive_path: Path) -> tuple[dict, dict[str, bytes]]:
    payload: dict[str, bytes] = {}
    with zipfile.ZipFile(archive_path, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or MANIFEST_NAME not in names:
            raise ValueError("Transfer archive has duplicate entries or no manifest")
        for info in infos:
            if info.is_dir() or _zip_is_link(info):
                raise ValueError("Transfer archive may contain only regular files")
            if info.file_size > _MAX_FILE:
                raise ValueError("Transfer archive entry exceeds size limit")
            if info.filename != MANIFEST_NAME:
                if not info.filename.startswith("payload/"):
                    raise ValueError("Unexpected transfer archive entry")
                _relative(info.filename[len("payload/") :], "archive payload")
        if sum(info.file_size for info in infos) > _MAX_TOTAL:
            raise ValueError("Transfer archive exceeds total size limit")
        manifest = json.loads(archive.read(MANIFEST_NAME))
        if not isinstance(manifest, dict):
            raise TypeError("Transfer manifest must be an object")
        for info in infos:
            if info.filename != MANIFEST_NAME:
                payload[info.filename[len("payload/") :]] = archive.read(info)
    return manifest, payload


def verify_transfer_package(archive_path: Path) -> dict:
    archive_path = Path(archive_path)
    archive_before = _file_sha(archive_path)
    manifest, payload = _read_archive(archive_path)
    required = {
        "schema_version",
        "manifest_sha256",
        "spec_sha256",
        "source",
        "files",
        "external_data",
        "credentials_required",
        "path_mappings",
        "claims",
    }
    if set(manifest) != required or manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise ValueError("Invalid transfer manifest fields")
    if manifest.get("manifest_sha256") != _manifest_hash(manifest):
        raise ValueError("Transfer manifest digest mismatch")
    if not isinstance(manifest["files"], list):
        raise TypeError("Transfer manifest files must be an array")
    spec_files = []
    for row in manifest["files"]:
        if not isinstance(row, dict) or set(row) != {
            "category",
            "source",
            "target",
            "size",
            "sha256",
        }:
            raise ValueError("Invalid transfer manifest file row")
        spec_files.append({key: row[key] for key in ("category", "source", "target")})
    reconstructed = {
        "schema_version": SPEC_SCHEMA,
        "source": manifest["source"],
        "files": spec_files,
        "external_data": manifest["external_data"],
        "credentials_required": manifest["credentials_required"],
        "path_mappings": manifest["path_mappings"],
    }
    _validate_spec(reconstructed)
    if _sha(_canonical(reconstructed)) != manifest["spec_sha256"]:
        raise ValueError("Transfer spec digest mismatch")
    expected = {row["target"]: row for row in manifest["files"]}
    if set(payload) != set(expected):
        raise ValueError("Transfer payload set does not match manifest")
    for target, raw in payload.items():
        row = expected[target]
        _relative(target, "manifest target")
        if _sensitive_path(Path(target)) or _SENSITIVE_CONTENT.search(raw):
            raise ValueError("Transfer archive contains credential/private material")
        if row["size"] != len(raw) or row["sha256"] != _sha(raw):
            raise ValueError(f"Transfer payload digest mismatch: {target}")
    if manifest["claims"] != {
        "credentials_packaged": False,
        "external_data_complete": not bool(manifest["external_data"]),
        "research_reproduced": False,
    }:
        raise ValueError("Transfer manifest claims are invalid")
    if _file_sha(archive_path) != archive_before:
        raise ValueError("Transfer archive changed during verification")
    return manifest


def restore_transfer_package(archive_path: Path, destination: Path) -> dict:
    """Verify then atomically restore into an absent destination directory."""
    archive_path = Path(archive_path).resolve()
    destination = Path(destination).resolve()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Restore destination must not exist")
    archive_sha = _file_sha(archive_path)
    manifest = verify_transfer_package(archive_path)
    _, payload = _read_archive(archive_path)
    if _file_sha(archive_path) != archive_sha:
        raise ValueError("Transfer archive changed during restore")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.restore-", dir=destination.parent))
    try:
        for target, raw in payload.items():
            output = _inside(staging, target, "restore target")
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists() or output.is_symlink():
                raise ValueError(f"Restore would overwrite a file: {target}")
            with output.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        for row in manifest["files"]:
            restored = _inside(staging, row["target"], "restored payload")
            if _has_symlink(staging, restored) or _sha(restored.read_bytes()) != row["sha256"]:
                raise ValueError(f"Restored payload verification failed: {row['target']}")
        mappings = {
            label: str((destination / _relative(target, "path mapping target")).resolve())
            for label, target in manifest["path_mappings"].items()
        }
        generated = {
            "schema_version": "quant.transfer-path-mappings/v1",
            "generated_for": str(destination),
            "mappings": mappings,
            "historical_files_modified": False,
        }
        (staging / "migration-paths.generated.json").write_bytes(_canonical(generated))
        needs_configuration = bool(manifest["external_data"] or manifest["credentials_required"])
        status_payload = {
            "schema_version": STATUS_SCHEMA,
            "status": "needs_configuration" if needs_configuration else "verified",
            "archive_sha256": archive_sha,
            "manifest_sha256": manifest["manifest_sha256"],
            "integrity_status": "verified",
            "configuration_status": "needs_configuration" if needs_configuration else "verified",
            "missing_external_data": manifest["external_data"],
            "missing_credentials": manifest["credentials_required"],
            "research_reproduction_status": "not_run",
            "runtime_rebuild_status": "not_run",
            "claims": {
                "payload_hashes_verified": True,
                "credentials_restored": False,
                "research_reproduced": False,
                "complete_environment_reproduced": False,
            },
        }
        (staging / "MIGRATION_STATUS.json").write_bytes(_canonical(status_payload))
        staging.replace(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return status_payload
