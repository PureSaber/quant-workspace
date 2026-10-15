param(
    [Parameter(Mandatory=$true)][string]$Installation,
    [ValidateSet('status','start','stop','restart','activate','rollback','backup','verify-backup','restore-drill')]
    [string]$Action = 'status',
    [string]$Target,
    [string]$Cycle,
    [switch]$Execute,
    [string]$ExpectedPlanSha256
)
$ErrorActionPreference = 'Stop'
$installPath = (Resolve-Path -LiteralPath $Installation).Path
$install = Get-Content -LiteralPath $installPath -Raw | ConvertFrom-Json
if ($install.schema_version -ne 'quant.local-maintenance-installation/v1') {
    throw 'Unsupported maintenance installation file'
}
$toolPython = $install.tool_python
if (-not (Test-Path -LiteralPath $toolPython -PathType Leaf)) { throw 'Control interpreter is missing' }
if ($Action -in @('status','start','stop','restart','activate','rollback')) {
    $commandArgs = @('-I','-B','-X','utf8','-m','quant_workspace.local_control',
        '--config',$install.control_config,$Action)
    if ($Target) { $commandArgs += @('--target',$Target) }
    if ($Execute) {
        if (-not $ExpectedPlanSha256) { throw 'Review the preview first and provide -ExpectedPlanSha256' }
        $commandArgs += @('--execute','--expected-plan-sha256',$ExpectedPlanSha256)
    }
    & $toolPython @commandArgs
    exit $LASTEXITCODE
}
if ($Action -ne 'backup' -and -not $Cycle) { throw 'Select the Cycle returned by the backup preview' }
if ($Execute -and $Action -eq 'backup' -and (-not $Cycle -or -not $ExpectedPlanSha256)) {
    throw 'Backup execution requires the reviewed Cycle and ExpectedPlanSha256'
}
if (-not $Cycle) { $Cycle = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8) }
if ($Cycle -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$') { throw 'Invalid backup cycle name' }
$cycleRoot = [IO.Path]::GetFullPath($install.backup_cycles)
$cyclePath = [IO.Path]::GetFullPath((Join-Path $cycleRoot $Cycle))
if ([IO.Path]::GetDirectoryName($cyclePath) -ne $cycleRoot.TrimEnd('\','/')) {
    throw 'Backup cycle must remain directly inside the configured backup directory'
}
$cycleConfig = Join-Path $cyclePath 'config.json'
if (-not (Test-Path -LiteralPath $cycleConfig)) {
    if ($Action -ne 'backup' -or $Execute) { throw 'Reviewed backup cycle does not exist' }
    if (Test-Path -LiteralPath $cyclePath) { throw 'Cycle destination already exists; select a new cycle' }
    $backup = Get-Content -LiteralPath $install.backup_template -Raw | ConvertFrom-Json
    $backup.state_dir = Join-Path $cyclePath 'state'
    $backup.archive = Join-Path $cyclePath 'archive.zip'
    $backup.restore_destination = Join-Path $cyclePath 'restored'
    New-Item -ItemType Directory -Path $cyclePath | Out-Null
    $backup | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $cycleConfig -Encoding utf8
    # Studio displays this same cycle; archived cycle configs remain immutable.
    Copy-Item -LiteralPath $cycleConfig -Destination $install.display_backup_config
}
if ($Action -eq 'restore-drill' -and -not $Execute) {
    $backup = Get-Content -LiteralPath $cycleConfig -Raw | ConvertFrom-Json
    @{action=$Action;cycle=$Cycle;destination=$backup.restore_destination;
      next_step='Run the same command with -Execute to restore into this isolated new directory';
      production_activated=$false} | ConvertTo-Json
    exit 0
}
$nativeAction = switch ($Action) {
    'backup' { if ($Execute) { 'backup' } else { 'preview' } }
    'verify-backup' { 'verify' }
    'restore-drill' { 'restore-drill' }
}
$commandArgs = @('-I','-B','-X','utf8','-m','quant_workspace.operational_backup',
    '--config',$cycleConfig,$nativeAction)
if ($Execute -and $Action -eq 'backup') { $commandArgs += @('--expected-plan-sha256',$ExpectedPlanSha256) }
$raw = & $toolPython @commandArgs
$code = $LASTEXITCODE
if ($code -ne 0) { $raw; exit $code }
$result = ($raw -join "`n") | ConvertFrom-Json
$result | Add-Member -NotePropertyName maintenance_cycle -NotePropertyValue $Cycle
$result | Add-Member -NotePropertyName maintenance_config -NotePropertyValue $cycleConfig
$result | ConvertTo-Json -Depth 40
