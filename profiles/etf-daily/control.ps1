param(
    [Parameter(Mandatory=$true)][string]$ToolPython,
    [Parameter(Mandatory=$true)][string]$Config,
    [ValidateSet('status','start','stop','restart','activate','rollback')][string]$Action = 'status',
    [string]$Target,
    [switch]$Execute,
    [string]$ExpectedPlanSha256
)
$ErrorActionPreference = 'Stop'
$controlArgs = @('-I', '-X', 'utf8', '-m', 'quant_workspace.local_control', '--config', $Config, $Action)
if ($Target) { $controlArgs += @('--target', $Target) }
if ($Execute) {
    if ($ExpectedPlanSha256 -notmatch '^[a-f0-9]{64}$') {
        throw 'Execution requires the exact SHA-256 from a reviewed preview.'
    }
    $controlArgs += @('--execute', '--expected-plan-sha256', $ExpectedPlanSha256)
}
& $ToolPython @controlArgs
exit $LASTEXITCODE
