# Daily research integration profile

This Python 3.10 profile joins the decision producer, exact ledger, account import,
experiment registry, orchestration and report dashboard. `stack.json` lists immutable
repository inputs and hashes the install closure. Old release tags are unchanged;
this is a daily-watchlist research profile, not an all-market/L2 release certification.

From the directory containing your `quant-workspace` checkout:

```powershell
py -3.10 -m venv .venv-daily
.venv-daily/Scripts/python.exe -m pip install --no-deps -r quant-workspace/profiles/daily-research/requirements.lock
.venv-daily/Scripts/python.exe -m pip check
.venv-daily/Scripts/python.exe quant-workspace/profiles/daily-research/run.py
```

The lock includes the complete runtime/dev closure, so `--no-deps` deliberately
prevents re-resolving previously audited packages. Public package pins satisfy
every installed project's metadata; report-hub's exact versions govern shared
pandas/matplotlib/Arrow. AKShare is the strategy repository's audited metadata-only
wheel, referenced by immutable commit and SHA-256. Packages are installed as VCS
wheels, not editable imports of neighboring working trees.

Results appear in sibling `daily-runs/` and `daily-dashboard.html`. Inspect
`daily-runs/operation-latest.json` and `latest.json`; HTML is a generation-time
snapshot. An unavailable provider publishes a blocked decision and a failed
operation. Do not silently reuse an old ready card. Repeat a captured run with:

```powershell
.venv-daily/Scripts/python.exe quant-workspace/profiles/daily-research/run.py --inputs PATH_TO_IMMUTABLE_INPUTS --as-of 2026-09-18
```

`decision.yaml` freezes a four-stock paper study with registration before
2026-09-21 and holdout through 2026-12-31. After that date a new deployment needs
a genuinely future interval and a new study/account/output directory. Do not
backdate registration or change the existing study to make an evaluation pass.
The holdout is not yet observed, and personal holdings have not been supplied.

To import actual statements, follow quant-portfolio's `docs/STATEMENT_IMPORT.md`,
store files outside Git, and set `account_config` in `pipeline.yaml`. The synthetic
example is only an importer test; it is not the user's account. Daily simulations
may show `observe` or `paper_ready`; neither status sends broker orders.

Free source boundaries: the 000333 June 2026 distribution disagrees with captured
qfq history and blocks windows crossing it. ST/halt access can fail; this profile's
advisory mode labels it unverified. For strict refusal use a separately registered
profile with `trading_status: required`. No complete historical ST/delistings or
historical constituent database is implied. Dividend payments use gross amounts;
personal dividend tax and deferred receivables are not modeled.

The entry point does not create a scheduler. Run after close when provider data
have arrived. Preserve immutable inputs and database backups. After a hard crash,
check that no producer remains before removing its lock; see quant-pipeline's
`docs/DAILY_RESEARCH.md`. Roll back by restoring this entire profile and its lock;
never mix dependency revisions into an existing paper account.
