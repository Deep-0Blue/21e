# Start Cloudflare 1.1.1.1 DNS script (no admin). Ctrl+C to stop.
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\run-dns.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$pyScript = Join-Path $PSScriptRoot "force_dns_cloudflare.py"
if (-not (Test-Path -LiteralPath $pyScript)) {
    Write-Host "Cannot find: $pyScript" -ForegroundColor Red
    Write-Host "Clone or download the repo so py\force_dns_cloudflare.py exists."
    Read-Host "Press Enter to exit"
    exit 1
}

$python = $null
foreach ($name in @("py", "python", "python3")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) {
        $python = $cmd.Source
        break
    }
}

if (-not $python) {
    Write-Host "Python was not found on PATH." -ForegroundColor Red
    Write-Host "Install from https://www.python.org/downloads/"
    Write-Host "Enable: Add python.exe to PATH, then open a NEW PowerShell window."
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "Using: $python" -ForegroundColor DarkGray
Write-Host "Target: 1.1.1.1 + 1.0.0.1 on Wi-Fi. Leave this window open. Ctrl+C to stop." -ForegroundColor Cyan
Write-Host ""

& $python $pyScript --persist @args
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ""
    Write-Host "Script exited with code $code. Diagnostics:" -ForegroundColor Yellow
    & $python $pyScript --check
    Read-Host "Press Enter to exit"
}

exit $code
