"""Idle-state backup and independent restore drill, including external ETF inputs."""

import argparse
import hashlib
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def hashes(roots):
    result = {}
    for label, root in roots.items():
        root = root.resolve()
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("Backup source contains a link or escaping path")
            if path.is_file() and path.suffix not in {".lock", ".tmp"}:
                result[label + "/" + path.relative_to(root).as_posix()] = (
                    path,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    roots = {k: Path(v) for k, v in json.loads(args.sources.read_text(encoding="utf-8")).items()}
    if (
        not roots
        or any(not p.is_dir() for p in roots.values())
        or any(not re.fullmatch(r"[a-z][a-z0-9-]*", k) for k in roots)
    ):
        raise ValueError("Sources require existing directories and simple archive labels")
    output = args.output.resolve()
    if output.exists() or any(output.is_relative_to(p.resolve()) for p in roots.values()):
        raise ValueError("Use a fresh output outside every source")
    for root in roots.values():
        for path in root.rglob("*.json"):
            if path.parent.name == ".jobs" or path.name == "operation.json":
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("status", record.get("state")) in {"queued", "running", "cancelling"}:
                    raise ValueError(
                        "Active or interrupted operation needs inspection before backup"
                    )
    before = hashes(roots)
    output.mkdir(parents=True)
    archive = output / (uuid.uuid4().hex + ".zip")
    manifest = {k: digest for k, (_, digest) in before.items()}
    with ZipFile(archive, "w", ZIP_DEFLATED) as zipped:
        for name, (path, _) in before.items():
            zipped.write(path, name)
        zipped.writestr("MANIFEST.json", json.dumps(manifest, sort_keys=True))
    if manifest != {k: digest for k, (_, digest) in hashes(roots).items()}:
        raise ValueError("Sources changed during backup; archive is unapproved")
    restored = output / "restore-drill"
    restored.mkdir()
    with ZipFile(archive) as zipped:
        for name, expected_digest in manifest.items():
            target = (restored / name).resolve()
            if not target.is_relative_to(restored):
                raise ValueError("Archive member escapes restore directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(name) as source, target.open("wb") as dest:
                shutil.copyfileobj(source, dest)
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected_digest:
                raise ValueError("Restored bytes differ from manifest")
    receipt = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "archive": str(archive),
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "files": len(manifest),
        "restore": str(restored),
        "verified": True,
        "scope": "All selected files restored and hashed; operational restart requires original path bindings",
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
