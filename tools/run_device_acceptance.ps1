[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Port,
    [switch]$DryRun,
    [switch]$TracerOnly,
    [string]$DryRunFailureStage = "",
    [ValidateRange(0, 60000)]
    [int]$BootWaitMs = 10000,
    [scriptblock]$MpremoteAdapter = $null
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$WorkspaceRoot = Split-Path -Parent $ProjectRoot
$Python = Join-Path $WorkspaceRoot ".venv\python.exe"

$Stages = @(
    [PSCustomObject]@{
        Name = "boot_probe"
        Script = "tools/device_boot_probe.py"
    },
    [PSCustomObject]@{
        Name = "runtime_target_tracer"
        Script = "tools/device_runtime_monitor.py"
    },
    [PSCustomObject]@{
        Name = "interaction_screen_tracer"
        Script = "tools/device_interaction_acceptance.py"
    }
)
if (-not $TracerOnly) {
    $Stages += [PSCustomObject]@{
        Name = "application_matrix"
        Script = "tools/device_application_acceptance.py"
    }
}

if (-not $DryRun -and $null -eq $MpremoteAdapter -and -not (Test-Path -LiteralPath $Python)) {
    throw "Missing project Python: $Python"
}

function Invoke-MpremoteCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$FailureMessage
    )

    if ($null -ne $MpremoteAdapter) {
        $AdapterResult = @(& $MpremoteAdapter $Arguments)
        if ($AdapterResult.Count -ne 1 -or $AdapterResult[0] -isnot [int]) {
            throw "MpremoteAdapter must return exactly one integer exit code"
        }
        $ExitCode = $AdapterResult[0]
    }
    else {
        & $Python -m mpremote @Arguments
        $ExitCode = $LASTEXITCODE
    }
    if ($ExitCode -ne 0) {
        throw $FailureMessage
    }
}

function Invoke-DeviceReset {
    if ($DryRun) {
        Write-Output "ACCEPTANCE_RESET $Port"
        return
    }

    Invoke-MpremoteCommand `
        -Arguments @("connect", $Port, "reset") `
        -FailureMessage "Unable to reset $Port after acceptance stage"
    if ($BootWaitMs -gt 0) {
        Start-Sleep -Milliseconds $BootWaitMs
    }
}

function Invoke-AcceptanceStage {
    param(
        [Parameter(Mandatory = $true)]
        [PSCustomObject]$Stage
    )

    $LocalScript = Join-Path $ProjectRoot $Stage.Script.Replace("/", "\")
    Write-Output "ACCEPTANCE_STAGE $($Stage.Name)"
    $StageFailure = $null
    try {
        if (-not (Test-Path -LiteralPath $LocalScript)) {
            throw "Missing device acceptance script: $LocalScript"
        }
        if ($DryRun) {
            Write-Output "ACCEPTANCE_COMMAND $Port $($Stage.Script)"
            if ($DryRunFailureStage -eq $Stage.Name) {
                throw "Simulated acceptance failure: $($Stage.Name)"
            }
        }
        else {
            Invoke-MpremoteCommand `
                -Arguments @("connect", $Port, "resume", "run", $LocalScript) `
                -FailureMessage "Acceptance stage failed: $($Stage.Name)"
        }
    }
    catch {
        $StageFailure = $_
    }
    finally {
        try {
            Invoke-DeviceReset
        }
        catch {
            if ($null -eq $StageFailure) {
                $StageFailure = $_
            }
            else {
                $ResetMessage = "Acceptance reset also failed: $($_.Exception.Message)"
                Write-Error $ResetMessage -ErrorAction Continue
            }
        }
    }

    if ($null -ne $StageFailure) {
        throw $StageFailure
    }
}

foreach ($Stage in $Stages) {
    Invoke-AcceptanceStage -Stage $Stage
}

if ($TracerOnly) {
    Write-Output "ACCEPTANCE_TRACERS_COMPLETE $Port"
}
else {
    Write-Output "ACCEPTANCE_COMPLETE $Port"
}
