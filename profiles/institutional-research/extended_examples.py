"""Additional synthetic account and calibration fixtures, never market evidence."""

import json
from datetime import timedelta
from decimal import Decimal


def extend(root, contracts):
    from quant_data_kit.derivatives import Quote, write_bundle
    from quant_data_kit.derivatives.models import utc
    from quant_options.pricing import price

    def save(name, obj):
        (root / name).write_text(
            json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    option_times = [utc(f"2025-01-{day:02}T15:00:01Z") for day in (7, 8, 9)]
    for c in contracts:
        quotes = []
        for at, spot in zip(option_times, (100, 102, 101), strict=True):
            premium = Decimal(
                str(
                    price(
                        spot,
                        100,
                        (c.expiry - at).total_seconds() / 31536000,
                        0.25,
                        right=c.option_right,
                    )
                )
            ).quantize(Decimal(".01"))
            quotes.append(
                Quote(
                    c.instrument_id,
                    at - timedelta(seconds=1),
                    at,
                    at.date().isoformat(),
                    premium,
                    premium + 1,
                    max(Decimal(0), premium - 1),
                    premium,
                    Decimal(100),
                    bid=premium - Decimal(".05"),
                    ask=premium + Decimal(".05"),
                    underlying_price=Decimal(spot),
                )
            )
        write_bundle(
            root / (c.venue.lower() + "-history"),
            (c,),
            quotes,
            provider="synthetic-local",
            evidence_kind="synthetic",
            rights_note="Generated fixture",
            limits="Three synchronized observations; not market historical coverage",
        )
    borrow = {
        "DEMO-SPOT": {
            "available_at": "2025-01-01T00:00:00Z",
            "expires_at": "2025-02-01T00:00:00Z",
            "units": 1000,
            "source": "synthetic-borrow",
        }
    }
    save(
        "options-replay.json",
        {
            "bundles": [c.venue.lower() + "-history" for c in contracts],
            "positions": {contracts[0].instrument_id: 2, contracts[1].instrument_id: -1},
            "initial_cash": 100000,
            "decision_at": "2025-01-06T21:00:01Z",
            "exit_at": option_times[-1].isoformat(),
            "borrow_rate": ".02",
            "financing_rate": ".03",
            "fee_per_unit": ".01",
            "hedge_rules": {
                "DEMO-SPOT": {
                    "available_at": "2025-01-01T00:00:00Z",
                    "venue": "SYNTH",
                    "tick": ".01",
                    "lot_size": 1,
                    "max_abs_position": 1000,
                }
            },
            "timeline": [
                {
                    "at": (at - timedelta(seconds=1)).isoformat(),
                    "available_at": at.isoformat(),
                    "settles_at": (at + timedelta(days=1)).isoformat(),
                    "borrow": borrow,
                    "option_capacity": {c.instrument_id: 100 for c in contracts},
                    "hedges": {
                        "DEMO-SPOT": {
                            "bid": str(Decimal(spot) - Decimal(".01")),
                            "ask": str(Decimal(spot) + Decimal(".01")),
                            "bid_size": 1000,
                            "ask_size": 1000,
                        }
                    },
                }
                for at, spot in zip(option_times, (100, 102, 101), strict=True)
            ],
        },
    )
    events = json.loads((root / "merger-events.json").read_text("utf-8"))
    events.append(
        {**events[0], "revision": 2, "status": "completed", "available_at": "2025-01-07T15:00:00Z"}
    )
    save("merger-events.json", events)
    target, acq = events[0]["target"], events[0]["acquirer"]
    times = [utc(f"2025-01-{day:02}T14:30:01Z") for day in (7, 8, 9, 10, 13)]
    study = json.loads((root / "mergers.json").read_text("utf-8"))
    timeline = []
    for n, at in enumerate(times):
        quotes = {acq: {"bid": "49.99", "ask": "50.01", "bid_size": 1000, "ask_size": 1000}}
        if n < 3:
            quotes[target] = {"bid": "97.99", "ask": "98.01", "bid_size": 1000, "ask_size": 1000}
        timeline.append(
            {
                "at": (at - timedelta(seconds=1)).isoformat(),
                "available_at": at.isoformat(),
                "quotes": quotes,
                "settles_at": (at + timedelta(days=1)).isoformat(),
                "borrow": {
                    acq: {
                        "available_at": study["as_of"],
                        "expires_at": "2025-02-01T00:00:00Z",
                        "units": 1000,
                        "source": "synthetic-locate",
                    }
                },
            }
        )
    save(
        "merger-replay.json",
        {
            "study": study,
            "initial_cash": 100000,
            "fee_per_unit": ".01",
            "exit_at": times[-1].isoformat(),
            "timeline": timeline,
            "settlement": {
                "event_id": "SYNTH-DELIVERY-1",
                "instrument_id": target,
                "kind": "merger",
                "effective_at": times[3].isoformat(),
                "available_at": times[3].isoformat(),
                "currency": "USD",
                "source": "synthetic-legal-terms",
                "evidence_id": "SYNTH-TERMS-1",
                "ratio": "1",
                "cash_per_unit": "50",
                "target_id": acq,
                "cost_fraction": ".5",
                "target_mark": "50",
            },
        },
    )
    first = utc("2019-01-01T00:00:00Z")
    records = []
    for n in range(60):
        at = first + timedelta(days=28 * n)
        records.append(
            {
                "event_id": f"SYNTH-TRAIN-{n}",
                "feature_at": at.isoformat(),
                "available_at": at.isoformat(),
                "label_available_at": (at + timedelta(days=10)).isoformat(),
                "completed": int(n % 3 != 0),
                "features": {"cash_share": (n % 5) / 5, "days_pending": 15 + n % 9},
                "source": "synthetic-generated",
            }
        )
    save(
        "merger-training.json",
        {
            "evidence_kind": "synthetic",
            "features": ["cash_share", "days_pending"],
            "records": records,
        },
    )
