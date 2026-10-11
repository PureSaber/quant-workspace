"""Opt-in local-source runner; does not replace any frozen environment or release."""

import argparse
import importlib
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="做市、并购事件和期权组合研究入口")
    parser.add_argument("--repos-root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument(
        "workflow",
        choices=[
            "examples",
            "acceptance",
            "calibrate-fills",
            "market-making",
            "import-mergers",
            "mergers",
            "options",
            "merger-replay",
            "train-merger-probability",
            "options-replay",
        ],
    )
    args, remainder = parser.parse_known_args()
    for name in (
        "quant-data-kit",
        "quant-execution",
        "quant-options",
        "quant-us-equity",
        "quant-lab",
        "quant-factors",
        "quant-workspace",
        "quant-studio",
    ):
        source = (args.repos_root / name / "src").resolve()
        if not source.is_dir():
            parser.error(f"missing local checkout: {source}")
        sys.path.insert(0, str(source))
    if args.workflow == "examples":
        from examples import main as examples_main

        return examples_main(remainder)
    if args.workflow == "acceptance":
        from acceptance import main as acceptance_main

        return acceptance_main(remainder)
    module, prefix = {
        "market-making": ("quant_execution.market_making_cli", []),
        "calibrate-fills": ("quant_execution.market_making_calibration", []),
        "import-mergers": ("quant_us_equity.cli", ["import-mergers"]),
        "mergers": ("quant_us_equity.cli", ["mergers"]),
        "options": ("quant_options.cli", ["portfolio"]),
        "options-replay": ("quant_options.cli", ["portfolio-replay"]),
        "merger-replay": ("quant_us_equity.cli", ["merger-replay"]),
        "train-merger-probability": ("quant_us_equity.cli", ["train-merger-probability"]),
    }[args.workflow]
    return importlib.import_module(module).main(prefix + remainder)


if __name__ == "__main__":
    raise SystemExit(main())
