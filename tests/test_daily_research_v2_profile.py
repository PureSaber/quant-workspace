from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

PROFILE = Path(__file__).resolve().parents[1] / "profiles" / "daily-research-v2"


def test_v2_profile_is_a_separate_strict_portfolio_account():
    data = yaml.safe_load((PROFILE / "data.yaml").read_text(encoding="utf-8"))
    decision = yaml.safe_load((PROFILE / "decision.yaml").read_text(encoding="utf-8"))
    pipeline = yaml.safe_load((PROFILE / "pipeline.yaml").read_text(encoding="utf-8"))
    stack = json.loads((PROFILE / "stack.json").read_text(encoding="utf-8"))
    lock = PROFILE / "requirements.lock"

    assert pipeline["output"] == "daily-runs-v2"
    assert pipeline["decision_config"].endswith("daily-research-v2/decision.yaml")
    assert pipeline["alerts_file"].endswith("daily-dashboard-v2.alerts.json")
    assert decision["account_id"] == "daily-research-v2-account"
    assert decision["study"]["id"] == "ashare-momentum-volatility-risk-v2-2026q4"
    assert data["corporate_actions"]["required"] is True
    assert data["trading_status"]["required"] is True
    assert decision["trading_status"] == "required"
    assert all(item.get("industry") for item in decision["watchlist"])
    assert {
        "max_single_weight",
        "max_gross_weight",
        "min_cash_weight",
        "max_industry_weight",
        "max_turnover",
        "max_positions",
        "max_estimated_cost_rate",
    } <= set(decision["risk"])
    assert hashlib.sha256(lock.read_bytes()).hexdigest() == stack["requirements_sha256"]
    assert all(
        len(item["commit"]) == 40 and set(item["commit"]) <= set("0123456789abcdef")
        for item in stack["repositories"].values()
    )
    assert " @ git+" not in lock.read_text(encoding="utf-8")
