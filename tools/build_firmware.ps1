[CmdletBinding()]
param(
    [string]$MicroPythonRoot = "",
    [string]$MpyCross = "",
    [string]$IdfPath = "",
    [string]$IdfToolsPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$WorkspaceRoot = Split-Path -Parent $ProjectRoot
if (-not $MicroPythonRoot) {
    $MicroPythonRoot = Join-Path $WorkspaceRoot "micropython"
}
$MicroPythonRoot = [System.IO.Path]::GetFullPath($MicroPythonRoot)
$Esp32Port = Join-Path $MicroPythonRoot "ports\esp32"
$Manifest = Join-Path $ProjectRoot "firmware\manifest.py"
$QstrWrapper = Join-Path $ProjectRoot "tools\firmware_qstr_wrapper.py"
$WorkRoot = Join-Path $ProjectRoot ".work"
$BuildRoot = Join-Path $WorkRoot "firmware\product"
$TempRoot = Join-Path $WorkRoot "temp"
$CompilerCache = Join-Path $WorkRoot "ccache"
if (-not $MpyCross) {
    $MpyCross = Join-Path $WorkRoot "tooling\mpy-cross-v1.28\mpy-cross.exe"
}

$ExpectedTag = "v1.28.0"
$ExpectedCommit = "e0e9fbb17ed6fd06bb76e266ae554784c9c80804"
$ExpectedTree = "6c48c290ce7e85916892549933ffea4daaedd331"

if (-not $IdfPath) {
    $IdfPath = Join-Path $env:USERPROFILE "esp\esp-idf-v5.5.2"
}
if (-not $IdfToolsPath) {
    $IdfToolsPath = Join-Path $env:USERPROFILE ".espressif"
}

$RequiredFiles = @(
    (Join-Path $IdfPath "export.bat"),
    (Join-Path $IdfPath "tools\idf.py"),
    $MpyCross,
    $Manifest,
    $QstrWrapper,
    (Join-Path $Esp32Port "CMakeLists.txt"),
    (Join-Path $MicroPythonRoot "lib\micropython-lib\README.md")
)
foreach ($Path in $RequiredFiles) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Missing firmware build dependency: $Path"
    }
}

$ActualCommit = (& git.exe -C $MicroPythonRoot rev-parse HEAD 2>$null | Out-String).Trim()
$ActualTree = (& git.exe -C $MicroPythonRoot rev-parse "HEAD^{tree}" 2>$null | Out-String).Trim()
if ($ActualCommit -ne $ExpectedCommit -or $ActualTree -ne $ExpectedTree) {
    throw (
        "MicroPython source is not locked to ${ExpectedTag}: " +
        "commit=$ActualCommit tree=$ActualTree")
}
$SourceChanges = (& git.exe -C $MicroPythonRoot status --porcelain `
    --untracked-files=no 2>$null | Out-String).Trim()
if ($SourceChanges) {
    throw "MicroPython $ExpectedTag source tree has tracked changes"
}
$MpyVersion = (& $MpyCross --version 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 `
        -or $MpyVersion -notmatch "MicroPython v1\.28\.0" `
        -or $MpyVersion -notmatch "mpy v6\.3" `
        -or $MpyVersion -match "preview") {
    throw "Expected stable MicroPython v1.28.0 mpy-cross emitting mpy v6.3: $MpyVersion"
}

$Python = Get-ChildItem -LiteralPath (Join-Path $IdfToolsPath "python_env") `
    -Directory -Filter "idf5.5_py*_env" |
    Sort-Object Name -Descending |
    ForEach-Object { Join-Path $_.FullName "Scripts\python.exe" } |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
if (-not $Python) {
    throw "Missing ESP-IDF 5.5 Python environment under $IdfToolsPath"
}
$Git = Get-Command git.exe -ErrorAction SilentlyContinue
if (-not $Git) {
    throw "Missing Git required by the ESP-IDF build"
}
$GitRoot = Split-Path -Parent (Split-Path -Parent $Git.Source)
$GitPosixBin = Join-Path $GitRoot "usr\bin"
foreach ($CommandName in @("touch.exe", "sed.exe", "cat.exe")) {
    $CommandPath = Join-Path $GitPosixBin $CommandName
    if (-not (Test-Path -LiteralPath $CommandPath -PathType Leaf)) {
        throw "Missing Git compatibility command: $CommandPath"
    }
}

New-Item -ItemType Directory -Force -Path $BuildRoot, $TempRoot, $CompilerCache | Out-Null

$PythonDir = Split-Path -Parent $Python
$IdfExport = Join-Path $IdfPath "export.bat"
$IdfPy = Join-Path $IdfPath "tools\idf.py"
$CommandPrefix = (
    'set "IDF_TOOLS_PATH=' + $IdfToolsPath + '"' +
    '&&set "PATH=' + $PythonDir + ';' + $GitPosixBin + ';%PATH%"' +
    '&&set "TEMP=' + $TempRoot + '"' +
    '&&set "TMP=' + $TempRoot + '"' +
    '&&set "TMPDIR=' + $TempRoot + '"' +
    '&&set "PYTHONPYCACHEPREFIX=' + (Join-Path $WorkRoot "pycache") + '"' +
    '&&set "CCACHE_DIR=' + $CompilerCache + '"' +
    '&&set "PYTHONUTF8=1"' +
    '&&set "PYTHONIOENCODING=utf-8"' +
    '&&call "' + $IdfExport + '" >nul' +
    '&&set "MICROPY_MPYCROSS=' + $MpyCross + '"' +
    '&&"' + $Python + '" "' + $IdfPy + '"' +
    ' -D MICROPY_BOARD=ESP32_GENERIC' +
    ' -D MICROPY_FROZEN_MANIFEST="' + $Manifest + '"' +
    ' -D COMPONENTS=main' +
    ' -B "' + $BuildRoot + '"'
)

function Set-QstrCommandAdapter {
    $CommandDirectory = Join-Path $BuildRoot "esp-idf\main\CMakeFiles"
    if (-not (Test-Path -LiteralPath $CommandDirectory -PathType Container)) {
        return 0
    }
    $Patched = 0
    $Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    foreach ($Batch in Get-ChildItem -LiteralPath $CommandDirectory -Filter "qstr*.bat") {
        $Lines = [System.IO.File]::ReadAllLines($Batch.FullName)
        for ($Index = 0; $Index -lt $Lines.Length; $Index++) {
            $Line = $Lines[$Index]
            $IsPreprocessor = $Line -like "*makeqstrdefs.py pp*"
            $LegacyShellFile = Join-Path $BuildRoot ($Batch.BaseName + ".sh")
            $IsLegacyPipeline = (
                $Line -like "*.sh*" -and
                (Test-Path -LiteralPath $LegacyShellFile -PathType Leaf))
            $IsPipeline = (
                ($Line -like "*qstrdefs.preprocessed.h*" -and
                 $Line -like "*| sed *") -or $IsLegacyPipeline)
            if (-not $IsPreprocessor -and -not $IsPipeline) {
                continue
            }
            $GuardIndex = $Line.LastIndexOf(" || (set FAIL_LINE=")
            if ($GuardIndex -lt 1) {
                throw "Unexpected qstr batch command: $($Batch.FullName)"
            }
            if ($IsLegacyPipeline) {
                $LegacyCommand = [System.IO.File]::ReadAllText($LegacyShellFile)
                $FirstNewline = $LegacyCommand.IndexOf("`n")
                if ($FirstNewline -lt 0) {
                    throw "Unexpected legacy qstr pipeline: $LegacyShellFile"
                }
                $Invocation = $LegacyCommand.Substring($FirstNewline + 1).TrimEnd()
            }
            else {
                $Invocation = $Line.Substring(0, $GuardIndex)
            }
            if ($IsPreprocessor) {
                $CommandFile = Join-Path $BuildRoot ($Batch.BaseName + ".command")
                [System.IO.File]::WriteAllText($CommandFile, $Invocation, $Utf8NoBom)
                $Lines[$Index] = (
                    '"' + $Python + '" "' + $QstrWrapper + '" "' +
                    $CommandFile + '"' + $Line.Substring($GuardIndex))
            }
            else {
                $CommandFile = Join-Path $BuildRoot ($Batch.BaseName + ".pipeline")
                [System.IO.File]::WriteAllText(
                    $CommandFile, $Invocation, $Utf8NoBom)
                $Lines[$Index] = (
                    '"' + $Python + '" "' + $QstrWrapper +
                    '" "--pipeline" "' + $CommandFile + '"' +
                    $Line.Substring($GuardIndex))
            }
            [System.IO.File]::WriteAllLines(
                $Batch.FullName, $Lines, [System.Text.Encoding]::ASCII)
            $Patched++
            break
        }
    }
    return $Patched
}

$FrozenContent = Join-Path $BuildRoot "frozen_content.c"
if ((Test-Path -LiteralPath $FrozenContent -PathType Leaf) -and
        ((Get-Item -LiteralPath $Manifest).LastWriteTimeUtc -gt
            (Get-Item -LiteralPath $FrozenContent).LastWriteTimeUtc -or
         (Get-Item -LiteralPath $MpyCross).LastWriteTimeUtc -gt
            (Get-Item -LiteralPath $FrozenContent).LastWriteTimeUtc)) {
    Remove-Item -LiteralPath $FrozenContent -Force
}

$Started = Get-Date
Push-Location $Esp32Port
try {
    if (-not (Test-Path -LiteralPath (Join-Path $BuildRoot "build.ninja") -PathType Leaf)) {
        & cmd.exe /d /c ($CommandPrefix + " reconfigure")
        if ($LASTEXITCODE -ne 0) {
            throw "ESP32 product firmware configure failed"
        }
    }

    $null = Set-QstrCommandAdapter
    & cmd.exe /d /c ($CommandPrefix + " build")
    $BuildExitCode = $LASTEXITCODE
    if ($BuildExitCode -ne 0 -and (Set-QstrCommandAdapter) -gt 0) {
        & cmd.exe /d /c ($CommandPrefix + " build")
        $BuildExitCode = $LASTEXITCODE
    }
    if ($BuildExitCode -ne 0) {
        throw "ESP32 product firmware build failed"
    }
}
finally {
    Pop-Location
}

$Application = Join-Path $BuildRoot "micropython.bin"
$PartitionTable = Join-Path $BuildRoot "partition_table\partition-table.bin"
if (-not (Test-Path -LiteralPath $Application -PathType Leaf)) {
    throw "Firmware build produced no application image: $Application"
}
if (-not (Test-Path -LiteralPath $PartitionTable -PathType Leaf)) {
    throw "Firmware build produced no partition table: $PartitionTable"
}

$ApplicationBytes = (Get-Item -LiteralPath $Application).Length
$FactoryBytes = 0x1F0000
if ($ApplicationBytes -gt $FactoryBytes) {
    throw "Application image exceeds the COM5 factory partition"
}
$Elapsed = [int]((Get-Date) - $Started).TotalMilliseconds
$Stream = [System.IO.File]::OpenRead($Application)
$Hasher = [System.Security.Cryptography.SHA256]::Create()
try {
    $HashBytes = $Hasher.ComputeHash($Stream)
}
finally {
    $Hasher.Dispose()
    $Stream.Dispose()
}
$Hash = [System.BitConverter]::ToString($HashBytes).Replace("-", "").ToLowerInvariant()
Write-Output (
    "FIRMWARE_BUILD product=sci-calc micropython=$ExpectedTag elapsed_ms=$Elapsed" +
    " app_bytes=$ApplicationBytes factory_bytes=$FactoryBytes sha256=$Hash")
Write-Output "FIRMWARE_APPLICATION $Application"
Write-Output "FIRMWARE_PARTITION_TABLE $PartitionTable"
