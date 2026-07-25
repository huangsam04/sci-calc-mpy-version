function Invoke-WithIsolatedPytestAddopts {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [scriptblock]$Action
    )

    $HadPytestAddopts = Test-Path Env:PYTEST_ADDOPTS
    $SavedPytestAddopts = $env:PYTEST_ADDOPTS
    try {
        Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
        & $Action
    }
    finally {
        if ($HadPytestAddopts) {
            $env:PYTEST_ADDOPTS = $SavedPytestAddopts
        }
        else {
            Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
        }
    }
}


function Invoke-DeviceToolCompilation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ToolsRoot,
        [Parameter(Mandatory = $true)]
        [string]$OutputRoot,
        [string]$MpyCross = "",
        [scriptblock]$CompilerAdapter = $null
    )

    $DeviceTools = @(
        Get-ChildItem -LiteralPath $ToolsRoot -Filter "device_*.py" -File
    )
    if ($DeviceTools.Count -eq 0) {
        throw "No device tools matched tools/device_*.py"
    }
    New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

    foreach ($Tool in $DeviceTools) {
        $Output = Join-Path $OutputRoot ($Tool.BaseName + ".mpy")
        if ($null -ne $CompilerAdapter) {
            $AdapterResult = @(& $CompilerAdapter $Tool.FullName $Output)
            if ($AdapterResult.Count -ne 1 -or $AdapterResult[0] -isnot [int]) {
                throw "CompilerAdapter must return exactly one integer exit code"
            }
            $ExitCode = $AdapterResult[0]
        }
        else {
            & $MpyCross -march=xtensawin -o $Output $Tool.FullName
            $ExitCode = $LASTEXITCODE
        }
        if ($ExitCode -ne 0) {
            throw "mpy-cross failed: tools\$($Tool.Name)"
        }
        if (-not (Test-Path -LiteralPath $Output -PathType Leaf)) {
            throw "mpy-cross did not create its output: $Output"
        }
        if ((Get-Item -LiteralPath $Output).Length -le 0) {
            throw "mpy-cross created an empty output: $Output"
        }
    }
}
