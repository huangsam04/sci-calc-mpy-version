param(
    [Parameter(Mandatory = $true)]
    [string]$SupportScript,
    [Parameter(Mandatory = $true)]
    [string]$ToolsRoot,
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,
    [switch]$CreateOutput,
    [switch]$CreateEmptyOutput,
    [int]$CompilerExitCode = 0
)

$ErrorActionPreference = "Stop"
. $SupportScript

$CompilerAdapter = {
    param([string]$Source, [string]$Output)
    if ($CreateOutput) {
        Set-Content -LiteralPath $Output -Value "compiled"
    }
    elseif ($CreateEmptyOutput) {
        New-Item -ItemType File -Force -Path $Output | Out-Null
    }
    return $CompilerExitCode
}

try {
    Invoke-DeviceToolCompilation `
        -ToolsRoot $ToolsRoot `
        -OutputRoot $OutputRoot `
        -CompilerAdapter $CompilerAdapter
}
catch {
    Write-Output "DEVICE_COMPILE_CAUGHT $($_.Exception.Message)"
    exit 97
}
