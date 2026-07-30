[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Port,
    [switch]$DryRun,
    [string]$DryRunFailureStage = "",
    [ValidateRange(0, 60000)]
    [int]$BootWaitMs = 25000,
    [scriptblock]$MpremoteAdapter = $null
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$WorkspaceRoot = Split-Path -Parent $ProjectRoot
$Python = Join-Path $WorkspaceRoot ".venv\python.exe"
$MpyCross = Join-Path `
    $WorkspaceRoot "micropython\mpy-cross\build\mpy-cross.exe"
$RemoteArtifact = "/sd/_sci_accept_stage.mpy"
$RunArtifact = (
    "import gc;gc.collect();import sys;sys.path.insert(0,'/sd');" +
    "import _sci_accept_stage;_sci_accept_stage.run()"
)
$PrepareResident = (
    "import gc,runtime_handle as h;gc.collect();" +
    "r=h.get_resident_runtime();assert r is not None;" +
    "r._binding_state[4].renderer.display.sleep();" +
    "print('ACCEPTANCE_RESIDENT_READY')"
)
$SleepResident = (
    "import runtime_handle as h;r=h.get_resident_runtime();" +
    "assert r is not None;r._binding_state[4].renderer.display.sleep();" +
    "print('ACCEPTANCE_OLED_SLEEP')"
)

$Stages = @(
    [PSCustomObject]@{
        Name = "boot_probe"
        Script = "tools/device_boot_probe.py"
        Artifact = ".mpy-build/device-tools/device_boot_probe.mpy"
    },
    [PSCustomObject]@{
        Name = "application_matrix"
        Script = "tools/device_application_acceptance.py"
        Artifact = ".mpy-build/device-tools/device_application_acceptance.mpy"
    },
    [PSCustomObject]@{
        Name = "runtime_target_tracer"
        Script = "tools/device_runtime_monitor.py"
        Artifact = ".mpy-build/device-tools/device_runtime_monitor.mpy"
    },
    [PSCustomObject]@{
        Name = "interaction_screen_tracer"
        Script = "tools/device_interaction_acceptance.py"
        Artifact = ".mpy-build/device-tools/device_interaction_acceptance.mpy"
    },
    [PSCustomObject]@{
        Name = "frame_allocation_probe"
        Script = "tools/device_frame_allocation_probe.py"
        Artifact = ".mpy-build/device-tools/device_frame_allocation_probe.mpy"
    }
)

if (-not $DryRun -and $null -eq $MpremoteAdapter -and -not (Test-Path -LiteralPath $Python)) {
    throw "Missing project Python: $Python"
}

function Build-AcceptanceArtifacts {
    if (-not (Test-Path -LiteralPath $MpyCross -PathType Leaf)) {
        throw "Missing MicroPython 1.29 mpy-cross: $MpyCross"
    }
    $MpyVersion = (& $MpyCross --version 2>&1 | Out-String)
    if ($MpyVersion -notmatch "v1\.29\.0-preview") {
        throw "Wrong mpy-cross version for device acceptance: $MpyVersion"
    }

    foreach ($Stage in $Stages) {
        $LocalScript = Join-Path `
            $ProjectRoot $Stage.Script.Replace("/", "\")
        $LocalArtifact = Join-Path `
            $ProjectRoot $Stage.Artifact.Replace("/", "\")
        if (-not (Test-Path -LiteralPath $LocalScript -PathType Leaf)) {
            throw "Missing device acceptance script: $LocalScript"
        }
        New-Item -ItemType Directory -Force `
            -Path (Split-Path -Parent $LocalArtifact) | Out-Null
        & $MpyCross -march=xtensawin -X no-source-lines `
            -s $Stage.Script -o $LocalArtifact $LocalScript
        if ($LASTEXITCODE -ne 0) {
            throw "mpy-cross failed for device acceptance: $($Stage.Script)"
        }
        if (
            -not (Test-Path -LiteralPath $LocalArtifact -PathType Leaf) -or
            (Get-Item -LiteralPath $LocalArtifact).Length -le 0
        ) {
            throw "mpy-cross did not create device tool: $LocalArtifact"
        }
    }
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
    if ($null -eq $MpremoteAdapter) {
        Invoke-MpremoteCommand `
            -Arguments @("connect", $Port, "resume", "exec", $SleepResident) `
            -FailureMessage "Unable to sleep the OLED after resetting $Port"
    }
}

function Invoke-AcceptanceStage {
    param(
        [Parameter(Mandatory = $true)]
        [PSCustomObject]$Stage
    )

    $LocalScript = Join-Path $ProjectRoot $Stage.Script.Replace("/", "\")
    $LocalArtifact = Join-Path `
        $ProjectRoot $Stage.Artifact.Replace("/", "\")
    Write-Output "ACCEPTANCE_STAGE $($Stage.Name)"
    $StageFailure = $null
    $ArtifactCopied = $false
    try {
        if (-not (Test-Path -LiteralPath $LocalScript)) {
            throw "Missing device acceptance script: $LocalScript"
        }
        if ($DryRun) {
            Write-Output "ACCEPTANCE_COMMAND $Port $($Stage.Artifact)"
            if ($DryRunFailureStage -eq $Stage.Name) {
                throw "Simulated acceptance failure: $($Stage.Name)"
            }
        }
        else {
            Invoke-MpremoteCommand `
                -Arguments @(
                    "connect", $Port, "resume", "exec", $PrepareResident
                ) `
                -FailureMessage (
                    "Resident runtime was not ready for acceptance stage: " +
                    $Stage.Name
                )
            if (
                $null -eq $MpremoteAdapter -and
                -not (Test-Path -LiteralPath $LocalArtifact)
            ) {
                throw "Missing compiled device acceptance tool: $LocalArtifact"
            }
            Invoke-MpremoteCommand `
                -Arguments @(
                    "connect", $Port, "resume", "fs", "cp",
                    $LocalArtifact, (":" + $RemoteArtifact)
                ) `
                -FailureMessage "Unable to upload acceptance stage: $($Stage.Name)"
            $ArtifactCopied = $true
            Invoke-MpremoteCommand `
                -Arguments @(
                    "connect", $Port, "resume", "exec", $RunArtifact
                ) `
                -FailureMessage "Acceptance stage failed: $($Stage.Name)"
        }
    }
    catch {
        $StageFailure = $_
    }
    finally {
        if ($ArtifactCopied) {
            try {
                Invoke-MpremoteCommand `
                    -Arguments @(
                        "connect", $Port, "resume", "fs", "rm",
                        (":" + $RemoteArtifact)
                    ) `
                    -FailureMessage (
                        "Unable to remove temporary acceptance artifact " +
                        "after $($Stage.Name)"
                    )
            }
            catch {
                if ($null -eq $StageFailure) {
                    $StageFailure = $_
                }
                else {
                    Write-Error `
                        "Acceptance cleanup also failed: $($_.Exception.Message)" `
                        -ErrorAction Continue
                }
            }
        }
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

if (-not $DryRun -and $null -eq $MpremoteAdapter) {
    Build-AcceptanceArtifacts
}

Invoke-DeviceReset

foreach ($Stage in $Stages) {
    Invoke-AcceptanceStage -Stage $Stage
}

if ($DryRun) {
    Write-Output "ACCEPTANCE_DRY_RUN_COMPLETE $Port stages=5"
}
else {
    Write-Output "ACCEPTANCE_COMPLETE $Port stages=5 animation=removed_heap_below_12k"
}
