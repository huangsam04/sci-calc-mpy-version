[CmdletBinding()]
param(
    [ValidateSet("base", "frozen")]
    [string]$Profile = "frozen",
    [string]$IdfPath = "",
    [string]$IdfToolsPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$WorkspaceRoot = Split-Path -Parent $ProjectRoot
$MicroPythonRoot = Join-Path $WorkspaceRoot "micropython"
$Esp32Port = Join-Path $MicroPythonRoot "ports\esp32"
$MpyCross = Join-Path $MicroPythonRoot "mpy-cross\build\mpy-cross.exe"
$Manifest = Join-Path $ProjectRoot ("firmware\manifest-" + $Profile + ".py")
$QstrWrapper = Join-Path $ProjectRoot "tools\firmware_qstr_wrapper.py"
$WorkRoot = Join-Path $ProjectRoot ".work"
$BuildRoot = Join-Path $WorkRoot ("firmware\" + $Profile)
$TempRoot = Join-Path $WorkRoot "temp"
$CompilerCache = Join-Path $WorkRoot "ccache"

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
        (Get-Item -LiteralPath $Manifest).LastWriteTimeUtc -gt
        (Get-Item -LiteralPath $FrozenContent).LastWriteTimeUtc) {
    Remove-Item -LiteralPath $FrozenContent -Force
}

$Started = Get-Date
Push-Location $Esp32Port
try {
    if (-not (Test-Path -LiteralPath (Join-Path $BuildRoot "build.ninja") -PathType Leaf)) {
        & cmd.exe /d /c ($CommandPrefix + " reconfigure")
        if ($LASTEXITCODE -ne 0) {
            throw "ESP32 firmware configure failed for profile $Profile"
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
        throw "ESP32 firmware build failed for profile $Profile"
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
    "FIRMWARE_BUILD profile=$Profile elapsed_ms=$Elapsed" +
    " app_bytes=$ApplicationBytes factory_bytes=$FactoryBytes sha256=$Hash")
Write-Output "FIRMWARE_APPLICATION $Application"
Write-Output "FIRMWARE_PARTITION_TABLE $PartitionTable"
