# ETF Windows scheduling

The ETF operations profile uses one daily business task and one Studio logon task.
`quant_workspace.etf_scheduler` renders Windows Task Scheduler XML and verifies its
installed action, working directory, slots and security settings. Registration uses
the current user's SID, `InteractiveToken` and `LeastPrivilege`; no password is stored.

Daily slots remain 16:30, 17:00, 18:00, 21:00 and 22:00 Asia/Shanghai. Logon also invokes
the same controller, whose real-clock admission gate skips expired windows. Use
`IgnoreNew`, no wake, no historical catchup and no scheduler hard termination. The
controller bounds individual native phases. The computer must be on and the user
logged in. Codex need not be running for business execution; Codex notifications may
wait until the app is available.

```text
python -m quant_workspace.etf_scheduler xml --config OPERATIONS_JSON --sid USER_SID --output TASK_XML
python -m quant_workspace.etf_scheduler status --config OPERATIONS_JSON
python -m quant_workspace.etf_scheduler start-studio --config CONTROL_JSON
```

Generated XML starts disabled. Register it only after reviewing the concrete paths.
The independent Studio launcher delegates to native local-control preview/execute,
checks the existing owner and listener, and never kills an unrelated port occupant.
The optional controller `scheduled_writer` binding identifies the approved runtime
and exact task specification. Switching to another runtime requires a verified,
disabled task; an unreadable task is not treated as disabled.

For rollback: pause the daily task, wait for in-flight work or explicitly recover an
interruption, preview the native software rollback, execute that preview and verify
the selected runtime. Do not restore an older database over new account observations.
Do not reactivate the old Codex business writer.

A temporary one-shot `probe` task can test actual Windows scheduling without calling
business code. Preserve the real trigger receipt and remove only that temporary task.
Direct script execution does not prove a scheduled trigger, and a simulated logon
does not prove an actual restart. Verify logon at the next natural user login.
