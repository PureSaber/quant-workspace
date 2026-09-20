# Daily research v2

This is a new paper account, not an upgrade in place of `daily-research`.
It writes to `daily-runs-v2`, uses distinct account/strategy IDs and registers the
`ashare-momentum-volatility-risk-v2-2026q4` study. Never point it at `daily-runs`.

The profile runs the exact clean local commits recorded in `stack.json`. This is a
local release candidate: the new commits have not been pushed or tagged. `verify.py`
rejects a missing, dirty or different checkout before the account is opened. Public
Python packages remain frozen in `requirements.lock`.

From the integration directory containing all repositories:

```powershell
py -3.10 -m venv .venv-daily-v2
.venv-daily-v2/Scripts/python.exe -m pip install --no-deps -r quant-workspace/profiles/daily-research-v2/requirements.lock
.venv-daily-v2/Scripts/python.exe -m pip check
.venv-daily-v2/Scripts/python.exe quant-workspace/profiles/daily-research-v2/run.py
```

For the already audited workspace, `.venv-provider-review` can run the same entry
point. The runner prepends only the verified local repository paths to `PYTHONPATH`;
it does not accept editable or uncommitted application code.

The workflow is conservative:

- corporate actions are required and reconciled against raw/qfq price behavior;
- current ST/halt evidence is required, must match the decision session and be no
  more than eight hours old;
- target orders are checked before emission for single-name, gross, cash, position
  count, turnover, estimated cost and industry concentration limits;
- next-session simulated fills are capped at 1% of the observed bar volume, so a
  large target is filled gradually instead of pretending unlimited liquidity;
- the dashboard publishes a machine-readable alert sidecar;
- `notification-latest.json` stays quiet for healthy/info-only runs and requests
  attention for failed/blocked runs or warning/critical alerts.

Inspect `daily-runs-v2/operation-latest.json`, `latest.json` and
`notification-latest.json`. The dashboard is `daily-dashboard-v2.html`. A status of
`paper_ready` permits only the next paper-account simulation; no code here can send
broker orders. `observe` means do not act. `blocked` means repair evidence or config
before another run.

The profile is scheduled for weekdays after the close. Local scheduled tasks require
the computer and Codex desktop app to be running. A missed invocation can be rerun
manually; the exclusive pipeline lock prevents two writers from advancing the same
account simultaneously.
