"""Run a synthetic dynamic walk-forward study through the verified multi-repo CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml

PROFILE = Path(__file__).resolve().parent


def smoke(root: Path, output: Path, account_policy: str = "independent") -> dict:
    output.mkdir(parents=True, exist_ok=False)
    entrypoint = [sys.executable, str(PROFILE / "run.py"), "--root", str(root)]

    def invoke(*args):
        subprocess.run([*entrypoint, *map(str, args)], check=True)

    invoke("verify")
    demo = output / "demo"
    invoke("demo", "--asset", "etf", "--advanced", "--output", demo)
    recipe_path = demo / "recipe.yaml"
    recipe = yaml.safe_load(recipe_path.read_text(encoding="utf-8"))
    recipe["validation"]["direction_policy"] = "train_ic"
    recipe["validation"]["account_policy"] = account_policy
    recipe["variants"] = []
    recipe_path.write_text(yaml.safe_dump(recipe, sort_keys=False), encoding="utf-8")
    invoke("plan", recipe_path)
    study = output / "study"
    invoke("run", recipe_path, "--output", study)
    before = json.loads((study / "study.json").read_text(encoding="utf-8"))
    if before["failed"] or len(before["results"]) < 4:
        raise RuntimeError("Expected complete base, buy-hold, cost and delayed-signal candidates")
    if not (study / "research.html").is_file() or not (study / "validation.html").is_file():
        raise RuntimeError("Research/validation report was not produced")
    for result in before["results"]:
        folds = result["validation"]["folds"]
        if len(folds) < 2 or any(fold["train"]["end"] >= fold["test"]["start"] for fold in folds):
            raise RuntimeError("Walk-forward windows violate the chronological contract")
        if account_policy == "independent" and len(result["risk_summary"]["test_folds"]) != len(
            folds
        ):
            raise RuntimeError("Missing fold risk evidence")
    artifact_hashes = {
        str(path.relative_to(study)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in study.rglob("result.json")
    }
    if not artifact_hashes:
        raise RuntimeError("No immutable candidate results")
    invoke("run", recipe_path, "--output", study)
    after = json.loads((study / "study.json").read_text(encoding="utf-8"))
    if before != after:
        raise RuntimeError("Resuming a completed study changed its results")
    for relative, expected in artifact_hashes.items():
        if hashlib.sha256((study / relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Resume rewrote immutable artifact: {relative}")
    cache_tamper_rejected = None
    if account_policy == "continuous":
        selected = study / "selected-continuous" / "selected-path.json"
        original = selected.read_bytes()
        payload = json.loads(original)
        if not payload["available"] or not payload.get("evidence_sha256"):
            raise RuntimeError("Missing verified selected-path replay evidence")
        payload["metrics"]["total_return"] = 99.0
        try:
            selected.write_text(json.dumps(payload), encoding="utf-8")
            rejected = subprocess.run(
                [*entrypoint, "run", str(recipe_path), "--output", str(study)],
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            cache_tamper_rejected = rejected.returncode != 0 and "selected path" in rejected.stderr
            if not cache_tamper_rejected:
                raise RuntimeError("Modified selected-path evidence was accepted on resume")
        finally:
            selected.write_bytes(original)
        invoke("run", recipe_path, "--output", study)
        if selected.read_bytes() != original:
            raise RuntimeError("Resume changed valid selected-path evidence")
    summary = {
        "scope": "synthetic-software-integration-only",
        "candidates": len(before["results"]),
        "failed": before["failed"],
        "resume_verified": True,
        "account_policy": account_policy,
        "cache_tamper_rejected": cache_tamper_rejected,
        "artifact_sha256": artifact_hashes,
    }
    (output / "smoke.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROFILE.parents[2])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--account-policy", choices=["independent", "continuous"], default="independent"
    )
    args = parser.parse_args()
    smoke(args.root.resolve(), args.output.resolve(), args.account_policy)
