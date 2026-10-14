# Local four-ETF Studio runtime

This profile installs the complete native research and simulated-account stack
into a new ordinary (non-editable) Python 3.12 environment. Existing shared
checkouts, paused accounts, input data and earlier environments remain separate.
`stack.json` pins all eleven repositories and the dependency-lock SHA256.
The workspace library pin is the existing tested library revision; these profile
scripts are copied separately into the new runtime and do not require a library change.

## Install and launch

```powershell
python profiles/etf-daily/bootstrap.py --root C:/QuantRuntime/etf-001 --source-root C:/QuantSource
& C:/QuantRuntime/etf-001/profile/start.ps1 -RuntimeRoot C:/QuantRuntime/etf-001 -StateRoot C:/QuantState/etf -DailyProfile C:/QuantConfig/etf-daily.json
```

Without `--source-root`, bootstrap uses the pinned PureSaber GitHub commits.
With it, each repository must contain the exact commit; uncommitted files are
never installed. Use a fresh runtime directory for upgrades. The launch script
verifies the installed VCS revisions, import paths and package hashes before
starting a hidden local-only Studio process. It refuses a port already in use.
The Studio settings file selects this Python for new research. If an existing
account is frozen to an older verified runtime, set `python_by_repo.quant-pipeline-frozen`
to that old executable. The daily bridge and account reader use that explicit
binding; the existing scheduler must use it too. Keep the account definition,
observation window, strategy, inputs and ledger unchanged. The connected registry
must share the selected frozen runtime. A missing explicit executable fails closed.

`/today` provides data update and native preflight. Connect each snapshot to an
existing project to retain the research card and prior experiments. Failed
candidates and earlier protocols remain visible. Explicit human decisions are
required before registering or replacing a simulated candidate.

## Backup and restore drill

Stop the service gracefully through its configured `stop-server.request`, wait
for the listener to close, and confirm no active tasks or native account writer.
Supply `backup.py` a JSON mapping of labels to absolute directories, including
Studio state, operator configuration, external QDK snapshots, daily receipts,
research evidence and all simulated-account roots. Example:

```json
{"state":"C:/QuantState/etf","config":"C:/QuantConfig","data":"D:/ETFData","operations":"D:/ETFOperations","accounts":"D:/ETFAccounts"}
```

```powershell
python profiles/etf-daily/backup.py --sources backup-sources.json --output D:/Backups/etf-001
```

The output must be new and outside all selected source trees. The script checks
for active jobs, creates a hash manifest, detects files changing during backup,
restores into a fresh drill directory and verifies every restored file. Then run
the native data/study/account readers against restored evidence. Frozen absolute
path bindings require restoring to the original paths during disaster recovery;
this drill does not silently rebind accounts to new paths. Preserve the matching
runtime and installed-stack receipt. Inspect and remove the exact stop marker
before restarting through `start.ps1`.

## Scheduling and acceptance

Register only one scheduler for `python -I -B -X utf8 -m quant_pipeline.etf_daily
tick --profile <operator-profile>`. The module enforces the Shanghai exchange
calendar. Legacy profiles use 16:30, 17:00 and 18:00; this deployment explicitly
configures optional `schedule` slots 16:30, 17:00, 18:00, 21:00 and 22:00 with
`collection_timeout_seconds: 600` and `timezone: Asia/Shanghai`. A killed
collection preserves evidence; the final failed slot ends that day's retries.
Manual and scheduled actions share the native operation lock and slot receipts.
Register new accounts with `--require-timely-orders` to require completed order
evidence before simulated next-open fills. Older frozen accounts are not modified.
Use the formal
Codex heartbeat when scheduling in the app; local automations require the app
and machine to remain running. The app scheduler may wake more often; duplicate
slots do not write a second observation. Keep it paused until candidate selection
and local acceptance are complete.

Historical revisions, evidence expiry, identity changes and ledger discrepancies
block observation. Refresh official master evidence instead of extending its
validity into unobserved future days. Native interrupted accounts need explicit
recovery; webpage job recovery is not substituted for it. Count only real automatic
trading-day receipts toward the first session plus five subsequent sessions.
Operational acceptance is independent of strategy validity.

## Optimizer v2 release and migration

The pinned stack uses CVXPY with OSQP for quadratic problems and Clarabel for
conic problems. New research can start from
`../research-workbench/templates/etf-optimizer-v2.yaml`, using **this profile's
new ordinary installation**, not the older research-workbench bootstrap. Fill in
real immutable input paths and the intended study dates before running it.
The template explicitly selects protocol 2, absolute risk, a five-trading-session
forecast horizon and mature rolling `ic_vol` forecasts. Choose `active` only with
`cost_aware` and complete benchmark weights. Explicit forecasts use the CSV
contract documented in quant-lab and a-share-multifactor. Saved v1 recipes retain
raw-score semantics and absolute risk; a benchmark alone never changes the
objective to active risk. Exact historical reproduction uses its original runtime.

The full account universe includes candidates, held assets and benchmark assets.
Candidates can receive new investments; excluded holdings target their known
unsellable/board-lot remainder. Benchmark-only assets remain at zero target.
Cash and unpaid dividends remain in NAV, but unpaid dividends cannot fund orders.
Post-fee targets are independently checked after deterministic local integer
repair. Failure keeps the original evidence and does not publish a substitute
target. No integer global-optimality claim is made.

Run each frozen comparison in a fresh output directory. The helper retains
requests, exact code/data identities, native certified ledgers, diagnostics and
failures. Do not overwrite an earlier failed replay with a later successful run.

```powershell
& C:/QuantRuntime/optimizer/env/Scripts/python.exe -I -X utf8 profiles/etf-daily/optimizer_acceptance.py --recipe C:/FrozenInputs/etf-barra.yaml --output C:/Acceptance/new/etf-barra --label optimizer-release
& C:/QuantRuntime/optimizer/env/Scripts/python.exe -I -X utf8 profiles/etf-daily/optimizer_compare.py --replays C:/Acceptance/new --previous C:/Acceptance/old --performance C:/Acceptance/performance --output C:/Acceptance/report
```

The comparison helper expects the two unchanged v1 shadow recipes as
`ashare-shadow`/`etf-shadow` in the new directory and `old-ashare`/`old-etf` in the
old directory. It independently recomputes net return, drawdown and exact costs
from native ledgers, checks equal shadow inputs/recipes, and reports actual
tracking error, turnover, risk calibration, active exposure, residuals and failure
counts. Performance files are generated by quant-portfolio's
`scripts/benchmark_optimizer.py` at 200, 1,000 and 6,000 assets / 25 factors.
Public historical prices lacking row-arrival and unrevised-vintage evidence are
explicitly retrospective. The existing market-factor statistical proxy is not
evidence for replacing Barra exposure definitions. Higher historical Sharpe is
not a release gate.

Release order is shared protocol and risk interfaces, optimizer, execution and
research consumers, reporting/UI, then this stack profile. CI must certify exact
source heads and locked installations. A source branch can depend on pre-existing
baseline changes; record that ancestry when reviewing and merging the stack.

## Reverting a software upgrade

1. Preserve the new runtime, launch receipt, failed operation and all new ledger
   records. Do not restore an older account archive over a newer ledger.
2. Request graceful Studio shutdown using its exact stop marker. Wait for its
   listener and workers to finish; inspect any interrupted native operation.
3. Restore only the saved Studio settings/runtime bindings for the verified old
   software. Run that runtime's `bootstrap.py --verify-installed` and the native
   read-only account inspection against the current ledger. If the current ledger
   cannot be read safely, keep execution stopped for native recovery.
4. Keep the single scheduler bound to the frozen account runtime. Confirm its
   profile hash and account definition before permitting the next scheduled tick.
5. Remove only the inspected stop marker and start the old software with its
   `start.ps1`; verify the listener, `/today`, account readout and launch receipt.

Record separate verdicts for software, exposure-model replacement and strategy
promotion. Six real consecutive trading-day receipts are a pending operational
gate until actually observed. A shadow replay, restore drill or manual backfill
cannot satisfy it.
