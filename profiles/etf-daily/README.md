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
The Studio settings file must select this same Python for all participating repos.

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
calendar, 16:30 initial slot and 17:00/18:00 acquisition retries. Use the formal
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
