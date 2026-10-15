param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [string]$LegacyRuntime
)
$ErrorActionPreference = 'Stop'
$repoDirectory = Split-Path $PSScriptRoot -Parent
New-Item -ItemType Directory -Force -Path $EvidenceDirectory | Out-Null
$evidenceRoot = (Resolve-Path $EvidenceDirectory).Path
$testTemp = Join-Path $evidenceRoot ('pytest-' + [guid]::NewGuid().ToString('N'))
if ($LegacyRuntime) { $env:QUANT_TEST_LEGACY_ROOT = (Resolve-Path $LegacyRuntime).Path }
Push-Location $repoDirectory
try {
    & $Python -m ruff check src tests
    if ($LASTEXITCODE) { throw 'Ruff check failed' }
    & $Python -m ruff format --check src tests
    if ($LASTEXITCODE) { throw 'Ruff format failed' }
    & $Python -m pytest tests/test_local_control.py tests/test_etf_release.py tests/test_operational_backup.py -q --basetemp=$testTemp 2>&1 |
        Tee-Object -FilePath (Join-Path $evidenceRoot 'local-delivery-tests.txt')
    if ($LASTEXITCODE) { throw 'Local delivery regression failed' }
} finally {
    Pop-Location
}
