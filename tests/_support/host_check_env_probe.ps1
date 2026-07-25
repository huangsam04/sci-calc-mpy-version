param(
    [Parameter(Mandatory = $true)]
    [string]$SupportScript
)

$ErrorActionPreference = "Stop"
. $SupportScript

Invoke-WithIsolatedPytestAddopts {
    if (Test-Path Env:PYTEST_ADDOPTS) {
        throw "PYTEST_ADDOPTS leaked into the pytest action"
    }
    Write-Output "PYTEST_ADDOPTS_INSIDE <unset>"
}

Write-Output "PYTEST_ADDOPTS_AFTER $env:PYTEST_ADDOPTS"
