param([Parameter(Mandatory=$true)][string]$RuntimeRoot,
      [Parameter(Mandatory=$true)][string]$StateRoot,
      [Parameter(Mandatory=$true)][string]$DailyProfile,
      [int]$Port=8770)
$ErrorActionPreference = 'Stop'
$runtimePath = (Resolve-Path -LiteralPath $RuntimeRoot).Path
$statePath = (Resolve-Path -LiteralPath $StateRoot).Path
$profilePath = (Resolve-Path -LiteralPath $DailyProfile).Path
$pythonPath = Join-Path $runtimePath 'env/Scripts/python.exe'
$bootstrapPath = Join-Path $runtimePath 'profile/bootstrap.py'
& $pythonPath -I $bootstrapPath --root $runtimePath --verify-installed | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Runtime identity verification failed' }
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) { throw "Port $Port already has an owner; inspect before starting another service" }
$env:QUANT_STUDIO_ETF_DAILY = $profilePath
$env:PYTHONPATH = $null
$env:PYTHONHOME = $null
$env:PYTHONUTF8 = '1'
$settingsPath = Join-Path $statePath 'studio-settings.json'
$runsPath = Join-Path $statePath 'runs'
$stopPath = Join-Path $statePath 'stop-server.request'
if (Test-Path -LiteralPath $stopPath) { throw 'A stop marker exists; inspect the previous service first' }
$processArgs = @('-I','-B','-X','utf8','-m','quant_studio','serve','--host','127.0.0.1',
    '--port',"$Port",'--runs-root',('"' + $runsPath + '"'),'--settings',('"' + $settingsPath + '"'),
    '--stop-file',('"' + $stopPath + '"'))
$process = Start-Process -FilePath $pythonPath -ArgumentList $processArgs -WindowStyle Hidden `
    -WorkingDirectory $statePath -RedirectStandardOutput (Join-Path $statePath 'server.out.log') `
    -RedirectStandardError (Join-Path $statePath 'server.err.log') -PassThru
@{pid=$process.Id;python=$pythonPath;profile=$profilePath;port=$Port;started_at=[DateTimeOffset]::Now.ToString('o')} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $statePath 'launch-receipt.json') -Encoding utf8
$process | Select-Object Id,StartTime,Path
