"""Compare frozen native optimizer replays; independently reconcile ledger metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def fixed(frame, name):
    return frame[name + "_units"].astype(float) * 10.0 ** -frame[name + "_scale"]


def summarize(directory):
    receipt = directory / "acceptance-result.json"
    record = read(receipt)
    row = {
        "case": directory.name,
        "status": record["status"],
        "receipt": str(receipt.resolve()),
        "receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
        "code_identity": record["code_identity"],
        "data_identity": record["data_identity"],
    }
    if record["status"] != "completed":
        return {**row, "error": record.get("error")}
    native = directory / "standard/v2"
    snapshots = pd.read_parquet(native / "portfolio_snapshots.parquet")
    snapshots["session"] = (
        pd.to_datetime(snapshots.event_time, utc=True).dt.tz_convert("Asia/Shanghai").dt.date
    )
    daily = (
        snapshots.sort_values("event_time")
        .drop_duplicates("session", keep="last")
        .set_index("session")
    )
    nav = fixed(daily, "nav")
    initial = record["recipe"]["costs"]["initial_capital"]
    total_return = nav.iloc[-1] / initial - 1
    with_initial = pd.Series([initial, *nav])
    drawdown = float((with_initial / with_initial.cummax() - 1).min())
    costs = pd.read_parquet(native / "costs.parquet")
    fee = sum(
        (Decimal(int(r.amount_units)).scaleb(-int(r.amount_scale)) for r in costs.itertuples()),
        Decimal(0),
    )
    metrics = record["result"]["metrics"]
    assert abs(total_return - metrics["total_return"]) < 1e-8
    assert abs(drawdown - metrics["max_drawdown"]) < 1e-8
    assert abs(float(fee) - metrics["cost_total"]) < 1e-8
    fills = pd.read_parquet(native / "fills.parquet")
    prior_nav = nav.shift(1).fillna(initial)
    notional = fixed(fills, "quantity") * fixed(fills, "price")
    fill_days = pd.to_datetime(fills.event_time, utc=True).dt.tz_convert("Asia/Shanghai").dt.date
    actual_turnover = sum(
        float(amount) / prior_nav.loc[day] for amount, day in zip(notional, fill_days)
    )
    summary = record["result"].get("optimizer_summary", {})
    accepted = [
        d
        for d in summary.get("decisions", [])
        if d.get("published") is not False and d.get("diagnostics", {}).get("backend") == "cvxpy"
    ]
    integer_residual = max(
        (
            max(
                d["diagnostics"]
                .get("execution", {})
                .get("constraint_residuals", {"none": 0})
                .values()
            )
            for d in accepted
        ),
        default=None,
    )
    row.update(
        interval=record["recipe"]["interval"],
        sessions=len(nav),
        net_return=float(total_return),
        max_drawdown=drawdown,
        fees_cny=str(fee),
        actual_two_sided_turnover=float(actual_turnover),
        actual_tracking_error=record["realized_risk"]["annualized_tracking_error"],
        engineering=record["engineering"],
        optimized_decisions=len(accepted),
        maximum_integer_residual=integer_residual,
        independent_ledger_reconciliation=True,
    )
    row["risk_calibration"] = calibrate(record, native, daily)
    return row


def calibrate(record, native, daily):
    """One-day risk calibration on actual close holdings, held fixed for the next session."""
    from a_share_multifactor.decision_workflow import load_inputs
    from a_share_multifactor.research_risk import causal_asset_returns
    from quant_portfolio.optimization import estimate_covariance

    cfg = record["recipe"].get("allocation", {})
    if cfg.get("forecast_horizon_sessions", 1) != 1:
        return {"status": "unavailable", "reason": "calibration requires one-session forecasts"}
    _, frames = load_inputs(Path(record["recipe"]["inputs"]["bundle"]))
    raw = frames["raw"].copy()
    raw["date"] = pd.to_datetime(raw.date)
    returns = causal_asset_returns(raw, frames.get("actions"))
    returns.index = pd.to_datetime(returns.index).date
    positions = pd.read_parquet(native / "positions.parquet")
    positions["value"] = fixed(positions, "base_market_value")
    last_times = set(daily.event_time)
    positions = positions.loc[positions.event_time.isin(last_times)].copy()
    positions["session"] = (
        pd.to_datetime(positions.event_time, utc=True).dt.tz_convert("Asia/Shanghai").dt.date
    )
    weights = (
        positions.pivot_table(
            index="session", columns="instrument_id", values="value", aggfunc="sum"
        )
        .reindex(index=daily.index, columns=returns.columns)
        .fillna(0)
    )
    weights = weights.div(fixed(daily, "nav"), axis=0)
    checks = {
        pd.Timestamp(c["session"]).date(): c["factor_risk"]
        for c in record["result"].get("optimizer_summary", {}).get("actual_risk_checks", [])
        if c.get("stage") == "realized" and c.get("factor_risk")
    }
    samples, exposure = [], []
    for day, w in weights.iterrows():
        # All observations at or before the forecast cutoff are the training set.
        past = returns.loc[returns.index <= day].tail(cfg.get("lookback", 60))
        future = returns.loc[returns.index > day]
        if (
            future.empty
            or future.index[0] > daily.index[-1]
            or len(past) < cfg.get("min_observations", 20)
        ):
            continue
        observed = float(w @ future.iloc[0])
        if day in checks:
            prediction = (
                checks[day]["portfolio_risk"]["total_variance"] / checks[day]["annualization"]
            )
            values = checks[day].get("active_exposures", {}).get("data", [])
            exposure.extend(abs(v) for v in values)
        elif record["recipe"].get("risk_model"):
            continue
        else:
            covariance = estimate_covariance(past, shrinkage=cfg.get("covariance_shrinkage", 0.2))
            prediction = float(w @ covariance @ w) / 252
        if prediction > 1e-16:
            samples.append(
                {
                    "session": str(day),
                    "next_session": str(future.index[0]),
                    "predicted_variance": prediction,
                    "observed_return": observed,
                }
            )
    if len(samples) < 2:
        return {
            "status": "unavailable",
            "samples": samples,
            "reason": "fewer than two nonzero-risk forecasts",
        }
    predicted = np.array([s["predicted_variance"] for s in samples])
    observed = np.array([s["observed_return"] for s in samples])
    return {
        "status": "computed",
        "forecast_count": len(samples),
        "method": "Barra realized-close snapshot"
        if checks
        else "rolling covariance on actual close holdings",
        "standardized_next_return_std": float(np.std(observed / np.sqrt(predicted), ddof=1)),
        "mean_squared_return_minus_forecast_variance": float(np.mean(observed**2 - predicted)),
        "max_absolute_active_factor_exposure": max(exposure, default=None),
        "samples": samples,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replays", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--performance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = [
        summarize(path)
        for path in sorted(args.replays.iterdir())
        if (path / "acceptance-result.json").exists()
    ]
    comparisons = []
    for dataset in ("ashare", "etf"):
        old = read(args.previous / f"old-{dataset}/acceptance-result.json")
        new = read(args.replays / f"{dataset}-shadow/acceptance-result.json")
        assert old["data_identity"] == new["data_identity"], "shadow input identities differ"
        assert old["recipe"] == new["recipe"], "shadow recipe differs"
        old_returns = pd.read_csv(args.previous / f"old-{dataset}/returns.csv", index_col=0)
        new_returns = pd.read_csv(args.replays / f"{dataset}-shadow/returns.csv", index_col=0)
        assert old_returns.index.equals(new_returns.index)
        difference = (old_returns.iloc[:, 0] - new_returns.iloc[:, 0]).abs()
        comparisons.append(
            {
                "dataset": dataset,
                "same_inputs_and_recipe": True,
                "changed_sessions": int((difference > 1e-12).sum()),
                "max_return_difference": float(difference.max()),
                "old_metrics": old.get("result", {}).get("metrics"),
                "new_metrics": new.get("result", {}).get("metrics"),
            }
        )
    performance = [read(path) for path in sorted(args.performance.glob("final-performance-*.json"))]
    report = {
        "schema": "quant.optimizer-acceptance/v1",
        "evidence_kind": "retrospective",
        "cases": rows,
        "shadow": comparisons,
        "performance": performance,
        "metric_definitions": {
            "net_return": "final exact ledger NAV / initial capital - 1",
            "max_drawdown": "minimum daily NAV / running peak including initial capital - 1",
            "fees_cny": "exact native costs ledger sum; execution-price slippage is already in NAV",
            "actual_two_sided_turnover": "sum absolute fill notional / prior session close NAV (initial NAV for first session)",
            "actual_tracking_error": "sample std of net daily return minus full four-asset equal-weight daily benchmark, annualized sqrt(252)",
            "risk_calibration": "each actual close portfolio is held fixed for the next observed session; training uses only returns through the close; standardized return std near 1 indicates calibration, not an investment acceptance threshold",
        },
        "limitations": [
            "Both samples select two assets with per-asset caps summing to the invested budget. These binding constraints leave essentially no allocation freedom; identical results cannot establish which optimizer or risk model is superior.",
            "Previously inspected public fixed-pool sample; historical row arrival timestamps and unrevised price vintages are unavailable.",
            "Barra comparison uses the existing one-market-factor statistical proxy. It is not validation of a vendor Barra model or promotion of new exposure definitions.",
            "No parameter selection from evaluation returns. IC labels mature before each decision; the evaluation remains retrospective.",
            "Five synthetic timing repetitions include first-use compilation; P95 is descriptive and not a live latency SLA.",
            "Six real trading-day operational receipts must be collected separately; historical replay does not satisfy that gate.",
        ],
    }
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    table = []
    for r in rows:
        if r["status"] != "completed":
            table.append(
                f'<tr><td>{escape(r["case"])}</td><td colspan="7">{escape(r.get("error", "failed"))}</td></tr>'
            )
            continue
        rate = r["engineering"]["failure_rate"]
        calibration = r["risk_calibration"].get("standardized_next_return_std")
        table.append(
            "<tr>"
            + "".join(
                f"<td>{escape(str(v))}</td>"
                for v in [
                    r["case"],
                    f"{r['net_return']:.2%}",
                    f"{r['max_drawdown']:.2%}",
                    f"{r['actual_tracking_error']:.2%}",
                    f"{float(r['fees_cny']):,.2f}",
                    f"{r['actual_two_sided_turnover']:.2f}",
                    "—" if rate is None else f"{rate:.1%}",
                    "—" if calibration is None else f"{calibration:.3f}",
                ]
            )
            + "</tr>"
        )
    html = """<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>优化器工程验收</title><style>body{font:16px/1.7 system-ui;max-width:1250px;margin:40px auto;padding:0 24px;color:#223344;background:#f8fafc}h1{font-size:30px}section{background:white;padding:24px;margin:20px 0;border:1px solid #dce3ec;border-radius:12px}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}td,th{padding:10px;text-align:right;border-bottom:1px solid #e3e8ef}td:first-child,th:first-child{text-align:left}.scroll{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}small{color:#526575}</style><h1>优化器与 Barra：工程验收及历史对照</h1><p>工程正确性、软件升级、模型替换和策略推广分别验收。历史收益高低不作为工程验收标准。</p><section><h2>固定输入的历史对照</h2><p>A 股：2026-07-27 至 2026-09-18；四 ETF：2025-05-22 至 2026-09-24。同一数据组使用相同初始资金、费用、信号与预算；simple_hold 为共同买入持有参考。shadow 保留旧配方用于版本对照。</p><div class="scroll"><table><thead><tr><th>样本 / 方法</th><th>净收益</th><th>最大回撤</th><th>实际跟踪误差</th><th>账本费用 / 元</th><th>双边换手</th><th>失败率</th><th>风险校准</th></tr></thead><tbody>"""
    html += (
        "".join(table)
        + "</tbody></table></div><p>风险校准为预测标准差标准化后的下一日固定持仓收益的样本标准差；接近 1 表示尺度较匹配。它不等于净收益夏普。费用列为实际手续费，成交滑点已包含于净值。</p></section>"
    )
    html += (
        "<section><h2>旧配置的影子对照</h2><ul>"
        + "".join(
            f"<li>{escape(c['dataset'])}：输入与配方摘要相同；{c['changed_sessions']} 个交易日收益发生变化，最大单日差 {c['max_return_difference']:.8%}。</li>"
            for c in comparisons
        )
        + "</ul><p>两套样本均选两只资产，单只上限之和等于总仓位，几乎没有分配空间。等权、协方差与因子风险方案结果相同，不能据此判断优化器或风险模型孰优。旧环境保留用于精确复现；新版差异须结合声明的求解与执行修复解释。</p></section>"
    )
    html += (
        "<section><h2>规模与耗时</h2><ul>"
        + "".join(
            f"<li>{p['assets']:,} 资产 / {p['factors']} 因子：P95 {p['p95_seconds']:.3f} 秒，进程峰值 {p['process_peak_memory_bytes'] / 1024**2:.1f} MiB；未构造完整资产协方差。</li>"
            for p in performance
        )
        + "</ul><p>每组 5 次，包含首次编译；6,000 资产用于扩展性验证，不作实盘时延承诺。</p></section>"
    )
    html += (
        "<section><h2>适用范围与未完成门槛</h2><ul>"
        + "".join("<li>" + escape(v) + "</li>" for v in report["limitations"])
        + '</ul><p>完整计算定义、逐日风险校准、运行提交及输入摘要保存在 <a href="report.json">report.json</a>。</p></section></html>'
    )
    (args.output / "report.html").write_text(html, encoding="utf-8")
    print(
        json.dumps(
            {
                "cases": len(rows),
                "failed_replays": sum(r["status"] != "completed" for r in rows),
                "report": str(args.output / "report.html"),
            }
        )
    )


if __name__ == "__main__":
    main()
