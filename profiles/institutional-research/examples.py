"""Generate tiny synthetic inputs for the three research workflows; no strategy is run."""

import argparse
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate(root):
    import pandas as pd
    from quant_data_kit.derivatives import Contract, Quote, write_bundle
    from quant_data_kit.derivatives.models import utc
    from quant_data_kit.us_research.prices import write_bundle as write_prices
    from quant_options.pricing import price

    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    at = utc("2025-01-06T21:00:01Z")
    observed = at - timedelta(seconds=1)
    expiry = utc("2025-03-21T20:00:00Z")
    contracts = []
    for venue, right in (("SYNTH-A", "put"), ("SYNTH-B", "call")):
        c = Contract(
            instrument_id=f"{venue}:{right}",
            symbol=f"DEMO-{right}",
            product="DEMO",
            venue=venue,
            currency="USD",
            kind="option",
            multiplier=Decimal(100),
            tick=Decimal("0.01"),
            listed_at=utc("2024-12-01T00:00:00Z"),
            known_at=utc("2024-12-01T00:00:00Z"),
            last_trade_at=expiry,
            expiry=expiry,
            timezone="America/New_York",
            settlement="cash",
            underlying="DEMO-SPOT",
            underlying_kind="spot",
            option_right=right,
            strike=Decimal(100),
            exercise_style="european",
            initial_margin=Decimal(3000),
            maintenance_margin=Decimal(2500),
            rules_source="synthetic demonstration",
        )
        contracts.append(c)
        premium = Decimal(
            str(price(100, 100, (expiry - at).total_seconds() / 31536000, 0.25, right=right))
        ).quantize(Decimal("0.01"))
        q = Quote(
            c.instrument_id,
            observed,
            at,
            "2025-01-06",
            premium,
            premium + 1,
            max(Decimal(0), premium - 1),
            premium,
            Decimal(100),
            bid=premium - Decimal("0.05"),
            ask=premium + Decimal("0.05"),
            underlying_price=Decimal(100),
        )
        write_bundle(
            root / venue.lower(),
            (c,),
            (q,),
            provider="synthetic-local",
            evidence_kind="synthetic",
            rights_note="Generated fixture, no vendor data",
            limits="Two quotes only; not complete chains or historical performance",
        )
    write_json(
        root / "options.json",
        {
            "bundles": ["synth-a", "synth-b"],
            "as_of": at.isoformat(),
            "base_currency": "USD",
            "positions": {contracts[0].instrument_id: 2, contracts[1].instrument_id: -1},
            "max_quote_age_hours": 1,
            "max_sync_seconds": 0,
            "scenarios": [
                {
                    "name": "spot -10%, IV +5pp",
                    "days": 1,
                    "spot_shocks": {"DEMO-SPOT": -0.1},
                    "volatility_shock": 0.05,
                },
                {"name": "spot +10%", "days": 1, "spot_shocks": {"DEMO-SPOT": 0.1}},
            ],
            "hedges": [
                {
                    "instrument_id": "DEMO-SPOT",
                    "underlying": "DEMO-SPOT",
                    "underlying_kind": "spot",
                    "currency": "USD",
                    "venue": "SYNTH-SPOT",
                    "multiplier": 1,
                    "lot_size": 1,
                    "tick": ".01",
                    "position": 0,
                    "max_abs_position": 1000,
                    "at": observed.isoformat(),
                    "available_at": at.isoformat(),
                    "bid": "99.99",
                    "ask": "100.01",
                    "fee_per_unit": ".01",
                    "execution_quotes": [
                        {
                            "at": "2025-01-06T21:00:02Z",
                            "available_at": "2025-01-06T21:00:02Z",
                            "bid": "99.98",
                            "ask": "100.02",
                            "bid_size": 100,
                            "ask_size": 100,
                        }
                    ],
                }
            ],
        },
    )
    rows = []
    for symbol, value in (("AAA", 98), ("BBB", 50)):
        rows.append(
            {
                "instrument_id": f"US:DEMO:{symbol}",
                "symbol": symbol,
                "session": "2025-01-06",
                "open": value,
                "high": value + 1,
                "low": value - 1,
                "close": value,
                "volume": 100000,
                "dividend": 0,
                "split_ratio": 1,
                "currency": "USD",
                "price_basis": "raw",
                "available_at": observed.isoformat(),
                "source": "synthetic-local",
            }
        )
    write_prices(
        root / "merger-prices",
        pd.DataFrame(rows),
        {
            "source": "synthetic-local",
            "observed_at": at.isoformat(),
            "evidence_kind": "synthetic",
            "universe_kind": "explicit-demo-watchlist",
        },
    )
    event = {
        "event_id": "SYNTH-MERGER-1",
        "revision": 1,
        "event_type": "merger",
        "target": "US:DEMO:AAA",
        "acquirer": "US:DEMO:BBB",
        "currency": "USD",
        "announced_at": "2025-01-03T22:00:00Z",
        "available_at": "2025-01-03T22:01:00Z",
        "expected_close_at": "2025-06-30T20:00:00Z",
        "status": "announced",
        "cash_per_share": "50",
        "exchange_ratio": "1",
        "source_ref": "synthetic://merger/1",
        "evidence_kind": "synthetic",
    }
    write_json(root / "merger-events.json", [event])
    write_json(
        root / "mergers.json",
        {
            "as_of": at.isoformat(),
            "assumptions_available_at": at.isoformat(),
            "deals": {
                event["event_id"]: {
                    "target_shares": 100,
                    "break_target_price": 70,
                    "break_acquirer_price": 55,
                    "completion_probability": ".9",
                    "financing_rate": ".03",
                    "borrow_rate": ".02",
                    "borrow_available": True,
                    "borrow_available_at": at.isoformat(),
                    "borrow_shares": 100,
                    "round_trip_cost": 5,
                }
            },
        },
    )
    fp = lambda units, scale=0: {"units": units, "scale": scale}
    instrument = {
        "instrument_id": "US:DEMO:ETF",
        "asset_class": "etf",
        "product_type": "us_etf",
        "venue": "SYNTHETIC",
        "native_symbol": "ETF",
        "settlement_currency": "USD",
        "quote_currency": "USD",
        "price_tick": fp(1, 2),
        "quantity_step": fp(1),
        "contract_multiplier": fp(1),
        "calendar_id": "XNYS",
        "effective_from": "2025-01-01T00:00:00Z",
        "available_at": "2025-01-01T00:00:00Z",
        "metadata": {"commission_rate": "0.0001", "market": "US"},
    }
    events = []
    start = utc("2025-01-06T15:00:00Z")
    for i, kind in enumerate(
        ("book_snapshot", "trade", "trade", "book_snapshot", "trade", "trade")
    ):
        timestamp = (start + timedelta(milliseconds=i * 200)).isoformat()
        row = {
            "event_type": kind,
            "event_id": f"synth-{i}",
            "instrument_id": instrument["instrument_id"],
            "event_time": timestamp,
            "received_at": timestamp,
            "available_at": timestamp,
            "source": "synthetic-local",
            "trading_day": "2025-01-06",
            "session_id": "synth-0106",
            "sequence": i,
        }
        if kind == "book_snapshot":
            row.update(
                bids=[{"price": fp(9999, 2), "quantity": fp(5), "order_count": None}],
                asks=[{"price": fp(10001, 2), "quantity": fp(5), "order_count": None}],
            )
        else:
            row.update(
                price=fp(9999 if i < 3 else 10001, 2),
                quantity=fp(10),
                aggressor_side="sell" if i < 3 else "buy",
            )
        events.append(row)
    write_json(
        root / "market-making.json",
        {
            "schema": "quant.market-making-input/1",
            "evidence_kind": "synthetic",
            "source_ref": "synthetic://inventory-quotes",
            "instrument": instrument,
            "initial_cash": fp(100000, 0),
            "matching": "l2",
            "latency_ms": 1,
            "policy": {
                "order_quantity": fp(10),
                "max_inventory": fp(20),
                "target_inventory": fp(10),
                "inventory_skew_ticks": 0,
                "refresh_ms": 50,
                "cancel_latency_ms": 10,
            },
            "events": events,
        },
    )
    from extended_examples import extend

    extend(root, contracts)
    return {"output": str(root), "evidence_kind": "synthetic", "workflows_executed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成离线合成研究输入")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(generate(args.output), ensure_ascii=False, indent=2))
    return 0
