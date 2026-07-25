[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $ProjectRoot
$Python = Join-Path $WorkspaceRoot ".venv\python.exe"
$MpyCross = Join-Path $WorkspaceRoot "micropython\mpy-cross\build\mpy-cross.exe"
$BuildRoot = Join-Path $ProjectRoot ".mpy-build"
$FontBuild = Join-Path $BuildRoot "fonts"
$HostCheckSupport = Join-Path $ProjectRoot "tools\host_check_support.ps1"

. $HostCheckSupport

if (-not (Test-Path -LiteralPath $MpyCross)) {
    throw "Missing MicroPython 1.29 mpy-cross: build it from ..\micropython\mpy-cross first"
}
$MpyVersion = (& $MpyCross --version 2>&1 | Out-String)
if ($MpyVersion -notmatch "v1\.29\.0-preview") {
    throw "Wrong mpy-cross version; expected repository MicroPython v1.29.0-preview: $MpyVersion"
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
    & $MpyCross -march=xtensawin -o $Output $_.FullName
    if ($LASTEXITCODE -ne 0) { throw "mpy-cross failed: $Relative" }
}

$DeviceToolBuild = Join-Path $BuildRoot "device-tools"
Invoke-DeviceToolCompilation `
    -ToolsRoot (Join-Path $ProjectRoot "tools") `
    -OutputRoot $DeviceToolBuild `
    -MpyCross $MpyCross

Write-Host "All host and MicroPython compatibility checks passed."
