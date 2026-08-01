[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Port,
    [ValidateSet(460800, 921600)]
    [int]$Baud = 921600,
    [string]$IdfToolsPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$WorkRoot = Join-Path $ProjectRoot ".work"
$Image = Join-Path $WorkRoot "firmware\product\micropython.bin"
$FactoryBytes = 0x1F0000

if (-not $IdfToolsPath) {
    $IdfToolsPath = Join-Path $env:USERPROFILE ".espressif"
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
if (-not (Test-Path -LiteralPath $Image -PathType Leaf)) {
    throw "Missing SCI-CALC product application image: $Image"
}
$ImageBytes = (Get-Item -LiteralPath $Image).Length
if ($ImageBytes -le 0 -or $ImageBytes -gt $FactoryBytes) {
    throw "Application image does not fit the factory partition: $ImageBytes bytes"
}

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
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null

$Hash = (Get-FileHash -LiteralPath $Image -Algorithm SHA256).Hash.ToLowerInvariant()
$Started = Get-Date
& $Python -m esptool `
    --chip esp32 `
    --port $Port `
    --baud $Baud `
    --before default_reset `
    --after hard_reset `
    write_flash `
    "0x10000" `
    $Image
if ($LASTEXITCODE -ne 0) {
    throw "SCI-CALC application flash failed on $Port"
}

$Elapsed = [int]((Get-Date) - $Started).TotalMilliseconds
Write-Output (
    "FIRMWARE_FLASH product=sci-calc port=$Port baud=$Baud" +
    " elapsed_ms=$Elapsed app_bytes=$ImageBytes offset=0x10000 sha256=$Hash")
}
finally {
    foreach ($Name in $ProcessEnvironmentNames) {
        [Environment]::SetEnvironmentVariable(
            $Name, $PreviousProcessEnvironment[$Name], "Process")
    }
}
