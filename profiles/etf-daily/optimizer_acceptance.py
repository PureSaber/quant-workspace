"""Frozen-input optimizer replay; every result or failure is retained separately."""

from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd


def main():
    from a_share_multifactor.research_workbench import EquityResearchExecutor, preflight_recipe
    from quant_lab.research import candidates, canonical, load_recipe
    from quant_pipeline.research_workbench import equity_code_identity, input_identity

    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    recipe = load_recipe(args.recipe)
    identity, data = equity_code_identity(), input_identity(recipe)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "request.json").write_text(
        canonical(
            {
                "recipe": recipe,
                "code_identity": identity,
                "data_identity": data,
                "label": args.label,
                "evidence_kind": "retrospective",
            }
        ),
        encoding="utf-8",
    )
    started = time.perf_counter()
    record = {
        "label": args.label,
        "status": "failed",
        "code_identity": identity,
        "data_identity": data,
        "recipe": recipe,
        "evidence_kind": "retrospective",
    }
    try:
        candidate = candidates(recipe)[0]
        check = preflight_recipe(recipe, candidate)
        (args.output / "preflight.json").write_text(canonical(check), encoding="utf-8")
        if not check["passed"]:
            raise ValueError("native input preflight failed; inspect preflight.json")
        result = EquityResearchExecutor()(recipe, candidate, args.output)
        record.update(status="completed", result=result)
        summary = result.get("optimizer_summary", {})
        decisions = summary.get("decisions", [])
        accepted = [
            d["diagnostics"]
            for d in decisions
            if d.get("published") is not False and "diagnostics" in d
        ]
        timings = [d.get("rebalance_seconds", d.get("total_seconds")) for d in accepted]
        timings = [t for t in timings if t is not None]
        record["engineering"] = {
            "decisions": len(decisions),
            "failed_decisions": summary.get("failed_decisions", 0),
            "failure_rate": None
            if not decisions
            else summary.get("failed_decisions", 0) / len(decisions),
            "p95_rebalance_seconds": None if not timings else float(np.quantile(timings, 0.95)),
            "max_constraint_residual": max(
                [d.get("max_constraint_residual", 0) for d in accepted], default=None
            ),
            "summed_rebalance_turnover": sum(d.get("turnover", 0) for d in accepted),
        }
        # Actual risk is measured from native, fee-inclusive ledger returns.
        returns = pd.read_csv(args.output / "returns.csv", index_col=0, parse_dates=True).iloc[:, 0]
        from a_share_multifactor.decision_workflow import load_inputs
        from a_share_multifactor.research_risk import causal_asset_returns

        _, frames = load_inputs(Path(recipe["inputs"]["bundle"]))
        raw = frames["raw"].copy()
        raw["date"] = pd.to_datetime(raw.date)
        asset_returns = causal_asset_returns(raw, frames.get("actions"))
        benchmark = asset_returns.mean(axis=1).reindex(returns.index)
        active = (returns - benchmark).dropna().iloc[1:]
        record["realized_risk"] = {
            "benchmark": "daily rebalanced equal weights in the frozen full four-asset universe",
            "annualized_tracking_error": float(active.std(ddof=1) * np.sqrt(252)),
            "observations": len(active),
        }
    except Exception as exc:  # noqa: BLE001 - retain failed replay evidence and exit nonzero
        record.update(
            status="failed", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()
        )
    record["seconds"] = time.perf_counter() - started
    (args.output / "acceptance-result.json").write_text(canonical(record), encoding="utf-8")
    print(
        json.dumps(
            {
                "label": args.label,
                "status": record["status"],
                "seconds": record["seconds"],
                "error": record.get("error"),
                "metrics": record.get("result", {}).get("metrics"),
            }
        ),
        flush=True,
    )
    return 0 if record["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
