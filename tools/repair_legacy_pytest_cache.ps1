[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ExpectedCache = Join-Path $ProjectRoot ".pytest_cache"
$ResolvedProject = [System.IO.Path]::GetFullPath($ProjectRoot)
$ResolvedCache = [System.IO.Path]::GetFullPath($ExpectedCache)

if ((Split-Path -Parent $ResolvedCache) -ne $ResolvedProject -or
    (Split-Path -Leaf $ResolvedCache) -ne ".pytest_cache") {
    throw "Refusing an unexpected pytest cache target: $ResolvedCache"
}

if (-not (Test-Path -LiteralPath $ResolvedCache -PathType Container)) {
    Write-Output "Legacy pytest cache is already absent."
    exit 0
}

$CacheItem = Get-Item -LiteralPath $ResolvedCache -Force
if ($CacheItem.LinkType) {
    throw "Refusing to repair a redirected pytest cache target."
}

& takeown.exe /F $ResolvedCache /R /D Y | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to take ownership of the legacy pytest cache."
}

$Account = "$env:USERDOMAIN\$env:USERNAME"
& icacls.exe $ResolvedCache /grant:r "$Account`:(OI)(CI)F" /T /C | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to grant access to the legacy pytest cache."
}

Remove-Item -LiteralPath $ResolvedCache -Recurse -Force
if (Test-Path -LiteralPath $ResolvedCache) {
    throw "Legacy pytest cache still exists after repair."
}

Write-Output "Removed the legacy pytest cache."
