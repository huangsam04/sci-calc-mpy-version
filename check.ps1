[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $ProjectRoot
$Python = Join-Path $WorkspaceRoot ".venv\python.exe"
$WorkRoot = Join-Path $ProjectRoot ".work"
$MpyCross = Join-Path $WorkRoot "tooling\mpy-cross-v1.28\mpy-cross.exe"
$BuildRoot = Join-Path $WorkRoot "mpy"
$FontBuild = Join-Path $BuildRoot "fonts"
$HostCheckSupport = Join-Path $ProjectRoot "tools\host_check_support.ps1"
$ProcessEnvironmentNames = @(
    "PYTHONPYCACHEPREFIX", "TEMP", "TMP", "TMPDIR")
$PreviousProcessEnvironment = @{}
foreach ($Name in $ProcessEnvironmentNames) {
    $PreviousProcessEnvironment[$Name] =
        [Environment]::GetEnvironmentVariable($Name, "Process")
}

try {
$env:PYTHONPYCACHEPREFIX = Join-Path $WorkRoot "pycache"
$env:TEMP = Join-Path $WorkRoot "temp"
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
New-Item -ItemType Directory -Force -Path $BuildRoot, $env:TEMP | Out-Null

. $HostCheckSupport

if (-not (Test-Path -LiteralPath $MpyCross)) {
    throw "Missing MicroPython v1.28.0 mpy-cross tool: $MpyCross"
}
$MpyVersion = (& $MpyCross --version 2>&1 | Out-String)
if ($MpyVersion -notmatch "MicroPython v1\.28\.0" `
        -or $MpyVersion -notmatch "mpy v6\.3" `
        -or $MpyVersion -match "preview") {
    throw "Wrong mpy-cross version; expected stable MicroPython v1.28.0 / mpy v6.3: $MpyVersion"
}

& $Python (Join-Path $ProjectRoot "tools\build_fonts.py") `
    --source-dir (Join-Path $ProjectRoot "source\fonts") `
    --output-dir $FontBuild
if ($LASTEXITCODE -ne 0) { throw "compact font asset generation failed" }

Invoke-WithIsolatedPytestAddopts {
    & $Python -m pytest (Join-Path $ProjectRoot "tests")
    if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
}

& $Python -m compileall -q (Join-Path $ProjectRoot "source")
if ($LASTEXITCODE -ne 0) { throw "CPython syntax compilation failed" }

Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "source") -Recurse -Filter "*.py" | ForEach-Object {
    $Relative = $_.FullName.Substring((Join-Path $ProjectRoot "source").Length + 1)
    $Output = Join-Path $BuildRoot ($Relative + ".mpy")
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null
    # The ESP32 port enables the Xtensa windowed native emitter, which is
    # required to compile the renderer's Viper transition compositor.
    & $MpyCross -march=xtensawin -s $Relative `
        -o $Output $_.FullName
    if ($LASTEXITCODE -ne 0) { throw "mpy-cross failed: $Relative" }
}

$DeviceToolBuild = Join-Path $BuildRoot "device-tools"
Invoke-DeviceToolCompilation `
    -ToolsRoot (Join-Path $ProjectRoot "tools") `
    -OutputRoot $DeviceToolBuild `
    -MpyCross $MpyCross

Write-Host "All host and MicroPython compatibility checks passed."
}
finally {
    foreach ($Name in $ProcessEnvironmentNames) {
        [Environment]::SetEnvironmentVariable(
            $Name, $PreviousProcessEnvironment[$Name], "Process")
    }
}
