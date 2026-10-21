# Research platform review follow-up

This is the implementation map for the October 2026 external review. Changes
are proposed independently so each repository keeps its supported dependency
lock and can validate its own runtime.

| Review item | Implementation or verified boundary |
| --- | --- |
| Futures panels keyed only by run ID | Build the panel for each backtest invocation; pass that exact panel to worker initializers. The registry no longer reads or writes persistent pickle files. |
| Private repository token inherited by build/test code | `profiles/institutional-research/ci.py` removes the raw token from every child environment. Only `git fetch` receives an authorization header, while preserving pre-existing Git settings. |
| Active HTML reports share the app origin | Studio sends report HTML with CSP `sandbox allow-scripts allow-downloads`, without `allow-same-origin`, and blocks forms/base URLs. |
| Flat Studio navigation and unclear demo provenance | Five navigation groups preserve existing routes. Template cards label declared synthetic defaults; unverified external input remains unverified. |
| Research conclusions and failed work hard to find | Studio's Research findings page indexes existing human decisions with their original project revisions and checked evidence. Lab's `findings` command inventories all registered studies, including unstarted, failed and retried attempts. |
| IC convention differs from next-open execution | A-share research defaults to next-open labels with corresponding maturity metadata. Reports disclose the basis and observed cross-section; legacy same-close diagnostics remain explicit. |

## Use the existing research contracts

`quant-lab findings --registry path/to/experiments.db --json` reads a TrialRegistry
without creating or migrating it, and checks file-backed results before exposing
their metrics. It does not interpret a successful process as a supported hypothesis.
Human stop/revise/observe/nominate decisions remain in Studio's existing DecisionStore.
Family-level DSR/PBO must come from the existing verified family-evidence path;
missing selection statistics are not invented from individual backtest scores.

The existing Lab recipe diagnostics already preregister cost multipliers and
signal delays. Use a frozen baseline plus 2x costs and an extra session of delay
in that same candidate family. Label shuffling and matched-universe randomization
require strategy-specific controls and declared seeds; they must not silently
change the input universe or become new post-hoc winner selection.

## Dependencies, interfaces and data

Factors imports Lab only through its optional research/dev extras; Lab's optional
rolling-research extra uses Factors without its research extra. This is not a
base-install dependency cycle. Keep tested commit/tag pins and lock files. A shared
release cohort should be validated with the existing stack-manifest/verify-stack
and acceptance profiles before its consumer pins are advanced together.

Different JSON hashes can belong to different versioned contracts. Changing their
serialization globally would invalidate stored identities. Unify helpers only
after establishing the same schema/byte contract and adding migration fixtures.

Studio is the primary research UI; durable background research uses Pipeline.
Report Hub remains useful for report rendering and existing consumers. Removing
older routes or replacing their job model needs a consumer migration and restart
tests; route cleanup alone is not evidence that saved jobs can resume correctly.

For daily cross-sectional research, expanding point-in-time history, delisted
coverage and historical membership is a research-data project. Four-name fixtures
validate software, not the full market. Record data coverage and source rights
explicitly; a synthetic or retrospective source must not be relabeled as PIT data.

## External CI prerequisites

A local test result cannot replace a required GitHub check. Keep required checks
and PR review in place. If Actions cannot start because of an account billing
limit, the account owner must resolve that limit. Private research acceptance
also needs an appropriately scoped `QUANT_RESEARCH_READ_TOKEN` repository secret.
Never substitute a developer's general GitHub token or silently skip private
acceptance to make a check green.
