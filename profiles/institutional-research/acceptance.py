"""Generate synthetic inputs and run all three Studio workflows end to end."""

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv=None):
    from examples import generate
    from quant_data_kit.us_research.corporate_events import import_events
    from quant_execution.derivative_replay import verify_artifacts
    from quant_studio.runner import run
    from quant_us_equity.merger_probability import save

    parser = argparse.ArgumentParser(description="离线端到端验收与报告生成")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / "inputs"
    generate(inputs)
    import_events(
        inputs / "events.sqlite", json.loads((inputs / "merger-events.json").read_text("utf-8"))
    )
    model = save(
        json.loads((inputs / "merger-training.json").read_text("utf-8")),
        "2024-01-01T00:00:00Z",
        inputs / "probability.json",
    )
    runtimes = output / "runtimes.json"
    runtimes.write_text(
        json.dumps(
            {
                "schema_version": "quant-studio.runtimes/v1",
                "python_by_repo": {"quant-institutional": sys.executable},
            }
        ),
        encoding="utf-8",
    )
    os.environ["QUANT_WORKSPACE_ROOT"] = str(Path(__file__).resolve().parents[3])
    os.environ["QUANT_STUDIO_RUNTIMES"] = str(runtimes)
    results = {
        "evidence_kind": "synthetic",
        "probability_evaluation_n": model["evaluation"]["n"],
        "workflows": {},
    }
    for workflow in (
        "inventory-market-making",
        "merger-event-research",
        "options-hedged-portfolio",
    ):
        result = run(workflow, {}, execute=True, runs_root=output / "runs", snapshot=inputs)
        results["workflows"][workflow] = result.as_json()
        if result.status != "succeeded":
            raise RuntimeError(json.dumps(result.as_json(), ensure_ascii=False))
        verify_artifacts(result.run_dir / "strategy-output")
    (output / "acceptance.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
