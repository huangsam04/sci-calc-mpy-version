param(
    [Parameter(Mandatory = $true)]
    [string]$Orchestrator,
    [Parameter(Mandatory = $true)]
    [string]$Port
)

$ErrorActionPreference = "Stop"
$Adapter = {
    param([string[]]$Arguments)

    Add-Content `
        -LiteralPath $env:SCI_CALC_FAKE_MPREMOTE_LOG `
        -Value ($Arguments -join "`t")
    if ($Arguments[-1] -eq "reset") {
        if ($env:SCI_CALC_FAKE_FAIL_RESET -eq "1") {
            return 91
        }
    }
    else {
        if ($env:SCI_CALC_FAKE_NOISY_STAGE -eq "1") {
            Write-Output "unexpected Adapter pipeline output"
        }
        if (
            (Split-Path -Leaf $Arguments[-1]) -eq
            $env:SCI_CALC_FAKE_FAIL_STAGE_SCRIPT
        ) {
            return 92
        }
    }
    return 0
}

try {
    & $Orchestrator `
        -Port $Port `
        -BootWaitMs 0 `
        -MpremoteAdapter $Adapter
}
catch {
    Write-Output "PROBE_CAUGHT $($_.Exception.Message)"
    exit 97
}
