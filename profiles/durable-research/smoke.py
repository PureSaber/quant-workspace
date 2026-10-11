"""Verify installed packages, independent workers and Studio submission/restart."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from copy import deepcopy
from pathlib import Path


def call(python: Path, *args: object, env: dict, cwd: Path) -> dict:
    result = subprocess.run(
        [str(python), *map(str, args)],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"command failed: {args}\n{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout)


def start_studio(python: Path, state: Path, env: dict) -> tuple[subprocess.Popen, str]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log = (state / "studio.log").open("ab")
    process = subprocess.Popen(
        [
            str(python),
            "-I",
            "-m",
            "quant_studio",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--runs-root",
            str(state / "studio-runs"),
            "--stop-file",
            str(state / "studio.stop"),
        ],
        env=env,
        cwd=state,
        stdout=log,
        stderr=log,
    )
    log.close()
    address = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while process.poll() is None and time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(address + "/durable-research", timeout=2) as response:
                if response.status == 200:
                    return process, address
        except OSError:
            time.sleep(0.1)
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=15)
    raise RuntimeError((state / "studio.log").read_text(encoding="utf-8"))


def stop_studio(process: subprocess.Popen, state: Path) -> None:
    (state / "studio.stop").touch()
    try:
        if process.wait(timeout=15) != 0:
            raise RuntimeError("Studio did not shut down cleanly")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    state = root / "acceptance"
    state.mkdir()
    python = Path(sys.executable)
    spec = importlib.util.spec_from_file_location(
        "durable_bootstrap", Path(__file__).with_name("bootstrap.py")
    )
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    identities = bootstrap.installed_identity()
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "QUANT_RESEARCH_REGISTRY_DB"}
    }
    env.update(
        QUANT_RESEARCH_STORE_ROOT=str(state / "artifacts"),
        QUANT_RESEARCH_CACHE_DB=str(state / "cache.db"),
    )
    fixture = root / "workspace/quant-lab/examples/rolling_research_fixture.py"
    call(
        python,
        "-I",
        fixture,
        "--store-root",
        state / "artifacts",
        "--output",
        state / "fixture",
        env=env,
        cwd=state,
    )
    requests = [
        json.loads((state / f"fixture/request-{window}.json").read_text("utf-8"))
        for window in (5, 10, 20)
    ]
    source = state / "fixture/synthetic.parquet"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    database = state / "jobs.db"
    config = {
        "schema_version": "quant-studio.durable-research/v1",
        "python": str(python),
        "database": str(database),
        "artifact_root": env["QUANT_RESEARCH_STORE_ROOT"],
        "cache_database": env["QUANT_RESEARCH_CACHE_DB"],
        "requests_root": str(state / "submitted"),
        "snapshots": {"synthetic": {"title": "固定合成数据：仅验证软件", "request": requests[0]}},
    }
    config_path = state / "durable-config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    env["QUANT_STUDIO_DURABLE_CONFIG"] = str(config_path)

    def client(*args):
        return call(
            python,
            "-I",
            "-m",
            "quant_pipeline.durable_client",
            "--database",
            database,
            *args,
            env=env,
            cwd=state,
        )

    def worker(handler="quant_lab.rolling_research:worker_handler"):
        return [
            str(python),
            "-I",
            "-m",
            "quant_pipeline.durable_worker",
            "--database",
            str(database),
            "--workspace",
            str(state / "attempts"),
            "--handler",
            f"rolling_research={handler}",
            "--artifact-guard",
            "quant_pipeline.durable_research:retain_artifacts",
            "--once",
        ]

    studio, address = start_studio(python, state, env)
    try:
        page = urllib.request.urlopen(address + "/durable-research", timeout=10).read().decode()
        fields = {
            name: re.search(rf'name="{name}"\s+value="([^"<>]+)"', page)[1]
            for name in ("_csrf_token", "snapshot", "revision", "key")
        }
        fields["window"] = "5"
        request = urllib.request.Request(
            address + "/durable-research/submit",
            data=urllib.parse.urlencode(fields).encode(),
            headers={"Origin": address},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            first_job = response.geturl().rsplit("/", 1)[1]
            assert "等待Worker" in response.read().decode()
        with urllib.request.urlopen(request, timeout=60) as response:
            assert response.geturl().endswith(first_job)
        assert len(client("list")) == 1
        stop_studio(studio, state)
        assert client("get", first_job)["status"] == "queued"
        completed = subprocess.run(
            worker(),
            env=env,
            cwd=state,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=True,
        )
        assert json.loads(completed.stdout)["status"] == "succeeded", completed.stderr
        (state / "studio.stop").unlink()
        studio, address = start_studio(python, state, env)
        reopened = urllib.request.urlopen(address + f"/durable-research/{first_job}", timeout=60)
        assert "已完成" in reopened.read().decode()
    finally:
        if studio.poll() is None:
            stop_studio(studio, state)
    for window in (10, 20):
        client(
            "submit",
            "--request",
            state / f"fixture/request-{window}.json",
            "--key",
            f"acceptance-{window}",
            "--research",
        )
        subprocess.run(worker(), env=env, cwd=state, check=True, timeout=120)
    repeated = deepcopy(requests[0])
    repeated["run_id"] = "installed-cache-reuse"
    repeated_path = state / "reuse.json"
    repeated_path.write_text(json.dumps(repeated), encoding="utf-8")
    job = client("submit", "--request", repeated_path, "--key", "reuse", "--research")
    subprocess.run(worker(), env=env, cwd=state, check=True, timeout=120)
    reused = client("get", job["job_id"])
    assert reused["result"]["cache"]["execution_mode"] == "cache_hit"
    # A trusted administrator selects an incompatible handler for one attempt.
    # The failed attempt must survive, then the normal handler retries the same job.
    retry = client("submit", "--request", repeated_path, "--key", "failure-retry", "--research")
    subprocess.run(
        worker("quant_lab.rolling_research:validate_payload"),
        env=env,
        cwd=state,
        check=True,
        timeout=120,
    )
    assert client("get", retry["job_id"])["status"] == "queued"
    subprocess.run(worker(), env=env, cwd=state, check=True, timeout=120)
    assert [row["status"] for row in client("attempts", retry["job_id"])] == ["failed", "succeeded"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with sqlite3.connect(state / "cache.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM factor_cache_inputs_v1").fetchone()[0] == 1
    jobs = client("list")
    assert len(jobs) == 5 and all(job["status"] == "succeeded" for job in jobs)
    from quant_data_kit.artifacts import ArtifactRef, ArtifactStore

    artifacts = ArtifactStore(state / "artifacts")
    for job in jobs:
        for ref in job["outputs"]:
            artifacts.verify(ArtifactRef.from_dict(ref))
    assert artifacts.collect(min_age_seconds=0) == []
    evidence = {
        "schema_version": "quant.durable-research-integration/v1",
        "status": "passed",
        "scope": "installed runtime, shared synthetic input, independent worker and Studio restart",
        "modules": identities,
        "source_sha256": before,
        "jobs": jobs,
        "attempts": {job["job_id"]: client("attempts", job["job_id"]) for job in jobs},
        "events": {job["job_id"]: client("events", job["job_id"]) for job in jobs},
        "market_data_certified": False,
        "live_orders_enabled": False,
    }
    (state / "acceptance.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"status": "passed", "jobs": len(jobs), "evidence": str(state / "acceptance.json")}
        )
    )


if __name__ == "__main__":
    main()
