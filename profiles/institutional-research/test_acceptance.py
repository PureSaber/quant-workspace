"""Cross-repository acceptance; run with the institutional research environment."""

import copy
import hashlib
import json
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from examples import generate
from quant_data_kit.derivatives.models import utc
from quant_data_kit.us_research.corporate_events import asof_events, import_events
from quant_execution.market_making_cli import evaluate
from quant_execution.research_account import ResearchBasketAccount
from quant_execution.us_cash import instrument
from quant_options.portfolio_replay import replay as option_replay
from quant_us_equity.merger_probability import predict, train
from quant_us_equity.merger_replay import replay as merger_replay


@pytest.fixture(scope="module")
def inputs(tmp_path_factory):
    root = tmp_path_factory.mktemp("research") / "inputs"
    generate(root)
    import_events(root / "events.sqlite", read(root, "merger-events.json"))
    return root


def read(root, name):
    return json.loads((root / name).read_text("utf-8"))


def test_market_maker_deterministic(inputs):
    raw = read(inputs, "market-making.json")
    first = evaluate(raw)
    assert first == evaluate(raw)
    assert len(first["fills"]) == 4
    assert first["final_account"]["positions"]["US:DEMO:ETF"]["units"] == 0


@pytest.mark.parametrize(
    "key,value", [("latency_ms", 0), ("evidence_kind", "live"), ("source_ref", "")]
)
def test_market_maker_invalid(inputs, key, value):
    raw = read(inputs, "market-making.json")
    raw[key] = value
    with pytest.raises(ValueError):
        evaluate(raw)


def test_market_maker_no_future_fills(inputs):
    raw = read(inputs, "market-making.json")
    raw["latency_ms"] = 10000
    assert not evaluate(raw)["fills"]


def test_events_asof_idempotency_and_conflict(inputs, tmp_path):
    rows = read(inputs, "merger-events.json")
    database = tmp_path / "events.sqlite"
    assert import_events(database, rows)["inserted"] == 2
    assert import_events(database, rows)["inserted"] == 0
    assert asof_events(database, "2025-01-06T22:00:00Z")[0]["revision"] == 1
    rows[1]["cash_per_share"] = "51"
    with pytest.raises(ValueError):
        import_events(database, rows)
    assert asof_events(database, "2025-02-01T00:00:00Z")[0]["cash_per_share"] == "50"


def test_probability_split_and_prediction(inputs):
    model = train(read(inputs, "merger-training.json"), as_of="2024-01-01T00:00:00Z")
    parts = model["split_event_ids"]
    assert [len(parts[k]) for k in ("training", "calibration", "evaluation")] == [36, 12, 12]
    assert not set(parts["training"]) & set(parts["evaluation"])
    assert sum(row["n"] for row in model["evaluation"]["calibration_bins"]) == 12
    assert (
        0 < predict(model, {"cash_share": 0.5, "days_pending": 18}, at="2025-01-01T00:00:00Z") < 1
    )
    with pytest.raises(ValueError, match="unavailable"):
        predict(model, {"cash_share": 0.5, "days_pending": 18}, at="2023-01-01T00:00:00Z")


@pytest.mark.parametrize(
    "mutation", ["duplicate", "nan", "outcome_before_feature", "small", "future"]
)
def test_probability_invalid(inputs, mutation):
    raw = read(inputs, "merger-training.json")
    if mutation == "duplicate":
        raw["records"].append(copy.deepcopy(raw["records"][0]))
    elif mutation == "nan":
        raw["records"][0]["features"]["cash_share"] = "NaN"
    elif mutation == "outcome_before_feature":
        raw["records"][0]["label_available_at"] = "2018-01-01T00:00:00Z"
    elif mutation == "small":
        raw["records"] = raw["records"][:20]
    else:
        for row in raw["records"]:
            row["label_available_at"] = "2026-01-01T00:00:00Z"
    with pytest.raises(ValueError):
        train(raw, as_of="2024-01-01T00:00:00Z")


def test_research_account_borrow_collateral_rollback():
    at = utc("2025-01-06T00:00:00Z")
    account = ResearchBasketAccount([instrument("US:X", "X", at)], 10000, at)
    args = {"at": at, "event_id": "short", "marks": {"US:X": Decimal(100)}}
    with pytest.raises(ValueError, match="borrow"):
        account.execute([("US:X", -10, 100)], **args)
    assert not account.fills and account.quantity("US:X") == 0
    borrow = {
        "US:X": {
            "available_at": at.isoformat(),
            "expires_at": (at + timedelta(days=1)).isoformat(),
            "units": 10,
            "source": "fixture",
        }
    }
    account.execute([("US:X", -10, 100)], borrow=borrow, **args)
    account.execute([("US:X", -10, 100)], borrow=borrow, **args)
    assert len(account.fills) == 1
    account.check_risk(args["marks"], at, borrow)
    with pytest.raises(ValueError, match="unexpired"):
        account.check_risk(args["marks"], at + timedelta(days=2), borrow)
    assert account.report()["ledger_balanced"]


def test_research_account_insufficient_cash():
    at = utc("2025-01-06T00:00:00Z")
    account = ResearchBasketAccount([instrument("US:X", "X", at)], 10, at)
    with pytest.raises(ValueError):
        account.execute([("US:X", 1, 100)], at=at, event_id="buy", marks={"US:X": Decimal(100)})
    assert account.quantity("US:X") == 0


def test_merger_stock_delivery_and_conservation(inputs):
    report, tables = merger_replay(
        inputs / "events.sqlite", inputs / "merger-prices", read(inputs, "merger-replay.json")
    )
    assert report["closed"] and report["delivered"] and report["ledger_balanced"]
    assert len(report["fills"]) == 4
    assert len(tables["nav.csv"]) == 6
    assert Decimal(tables["nav.csv"][-1]["nav"]) > 100000


@pytest.mark.parametrize("status", ["terminated", "completed"])
def test_merger_cash_resolution(inputs, tmp_path, status):
    events = read(inputs, "merger-events.json")
    for event in events:
        event.update(exchange_ratio="0", cash_per_share="100")
    events[1]["status"] = status
    database = tmp_path / "cash.sqlite"
    import_events(database, events)
    raw = read(inputs, "merger-replay.json")
    target = events[0]["target"]
    for tick in raw["timeline"]:
        tick["quotes"] = {
            target: {"bid": "97.99", "ask": "98.01", "bid_size": 1000, "ask_size": 1000}
        }
    if status == "completed":
        raw["settlement"] = {
            k: v
            for k, v in raw["settlement"].items()
            if k not in {"target_id", "target_mark", "cost_fraction"}
        }
        raw["settlement"].update(kind="terminal_cash", cash_per_unit="100", ratio="0")
    else:
        raw.pop("settlement")
        raw["timeline"][2]["quotes"][target].update(bid="69.99", ask="70.01")
    report, tables = merger_replay(database, inputs / "merger-prices", raw)
    assert report["closed"] and report["ledger_balanced"]
    assert report["delivered"] == (status == "completed")
    if status == "terminated":
        assert Decimal(tables["nav.csv"][-1]["nav"]) < 100000


@pytest.mark.parametrize("mutation", ["borrow", "future_quote", "wrong_delivery"])
def test_merger_rejects(inputs, mutation):
    raw = read(inputs, "merger-replay.json")
    if mutation == "borrow":
        raw["timeline"][0]["borrow"] = {}
    elif mutation == "future_quote":
        raw["timeline"][0]["available_at"] = "2025-01-01T00:00:00Z"
    else:
        raw["settlement"]["ratio"] = "2"
    with pytest.raises(ValueError):
        merger_replay(inputs / "events.sqlite", inputs / "merger-prices", raw)


def test_options_unified_account(inputs):
    report, tables = option_replay(read(inputs, "options-replay.json"), config_dir=inputs)
    assert report["closed"] and report["ledger_balanced"]
    assert len(report["fills"]) == 6 and len(tables["nav.csv"]) == 4
    assert any(fill["instrument_id"] == "DEMO-SPOT" for fill in report["fills"])
    assert all(utc(row["signal_available_at"]) < utc(row["at"]) for row in tables["decisions.csv"])


@pytest.mark.parametrize("mutation", ["cash", "sync", "limit", "future_rules", "negative_capacity"])
def test_options_rejects(inputs, mutation):
    raw = read(inputs, "options-replay.json")
    if mutation == "cash":
        raw["initial_cash"] = 1
    elif mutation == "sync":
        raw["timeline"][0]["at"] = "2025-01-07T14:59:59Z"
    elif mutation == "limit":
        raw["hedge_rules"]["DEMO-SPOT"]["max_abs_position"] = 1
    elif mutation == "future_rules":
        raw["hedge_rules"]["DEMO-SPOT"]["available_at"] = "2026-01-01T00:00:00Z"
    else:
        raw["timeline"][0]["option_capacity"]["SYNTH-A:put"] = -1
    with pytest.raises(ValueError):
        option_replay(raw, config_dir=inputs)


def test_options_partial_exit_is_disclosed(inputs):
    raw = read(inputs, "options-replay.json")
    raw["timeline"][-1]["option_capacity"] = dict.fromkeys(raw["positions"], 0)
    report, _ = option_replay(raw, config_dir=inputs)
    assert report["closed"] is False


def test_fill_model_calibration():
    from quant_execution.market_making_calibration import calibrate

    at = utc("2025-01-01T00:00:00Z")
    rows = [
        {
            "order_id": str(n),
            "submitted_at": (at + timedelta(days=n)).isoformat(),
            "outcome_available_at": (at + timedelta(days=n, seconds=1)).isoformat(),
            "quantity": 10,
            "filled_quantity": n % 3,
            "predicted_fills": {"conservative": n % 3, "optimistic": 10},
            "source": "synthetic",
        }
        for n in range(20)
    ]
    result = calibrate(
        {
            "evidence_kind": "synthetic",
            "candidates": {"conservative": {"latency_ms": 10}, "optimistic": {"latency_ms": 1}},
            "records": rows,
        },
        "2025-02-01T00:00:00Z",
    )
    assert result["selected_candidate"] == "conservative" and result["evaluation"]["n"] == 6


@pytest.mark.parametrize(
    "workflow", ["inventory-market-making", "merger-event-research", "options-hedged-portfolio"]
)
def test_studio_pipeline(inputs, tmp_path, monkeypatch, workflow):
    from quant_execution.derivative_replay import verify_artifacts
    from quant_studio.runner import run

    root = Path(__file__).resolve().parents[3]
    runtime = tmp_path / "runtimes.json"
    runtime.write_text(
        json.dumps(
            {
                "schema_version": "quant-studio.runtimes/v1",
                "python_by_repo": {"quant-institutional": sys.executable},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("QUANT_STUDIO_RUNTIMES", str(runtime))
    before = {
        str(p.relative_to(inputs)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in inputs.rglob("*")
        if p.is_file()
    }
    result = run(workflow, {}, execute=True, runs_root=tmp_path / "runs", snapshot=inputs)
    assert result.status == "succeeded", result.as_json()
    assert result.report and (result.run_dir / result.report).is_file()
    verify_artifacts(result.run_dir / "strategy-output")
    after = {
        str(p.relative_to(inputs)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in inputs.rglob("*")
        if p.is_file()
    }
    assert before == after
