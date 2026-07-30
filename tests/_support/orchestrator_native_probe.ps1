param(
    [Parameter(Mandatory = $true)]
    [string]$Orchestrator,
    [Parameter(Mandatory = $true)]
    [string]$Port
)

$ErrorActionPreference = "Stop"
$script:CurrentStageScript = ""
$script:ResetCount = 0
$Adapter = {
    param([string[]]$Arguments)

    Add-Content `
        -LiteralPath $env:SCI_CALC_FAKE_MPREMOTE_LOG `
        -Value ($Arguments -join "`t")
    if ($Arguments[-1] -eq "reset") {
        $script:ResetCount += 1
        if (
            $env:SCI_CALC_FAKE_FAIL_RESET -eq "1" -and
            (
                -not $env:SCI_CALC_FAKE_FAIL_STAGE_SCRIPT -or
                $script:ResetCount -gt 1
            )
        ) {
            return 91
        }
    }
    else {
        if ($Arguments -contains "cp") {
            $Artifact = Split-Path -Leaf $Arguments[-2]
            $script:CurrentStageScript = `
                [System.IO.Path]::ChangeExtension($Artifact, ".py")
        }
        if ($env:SCI_CALC_FAKE_NOISY_STAGE -eq "1") {
            Write-Output "unexpected Adapter pipeline output"
        }
        if (
            $Arguments -contains "exec" -and
            $script:CurrentStageScript -eq
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
    $OrchestratorExitCode = $LASTEXITCODE
    if ($null -ne $OrchestratorExitCode -and $OrchestratorExitCode -ne 0) {
        exit $OrchestratorExitCode
    }
}
catch {
    Write-Output "PROBE_CAUGHT $($_.Exception.Message)"
    exit 97
}
