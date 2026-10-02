<#
Build the current Windows x64 WinUI portable distribution.
The v0.5.x PyInstaller specs are retained only as historical Qt build recipes.
The package name/version and runtime contract live in scripts/package_winui.py.
#>
param(
    [string]$Python = 'python',
    [string]$Output = (Join-Path $PSScriptRoot 'release\winui-portable'),
    [string]$ReuseRuntime,
    [switch]$NoRestore,
    [switch]$SkipTests
)
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne 'Win32NT') {
    throw 'WinUI portable builds require Windows x64 and the .NET 10 SDK.'
}
$outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
$runtimePath = if ($ReuseRuntime) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ReuseRuntime)
} else { $null }
Push-Location $PSScriptRoot
try {
    if (-not $SkipTests) {
        & $Python -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw 'Tests failed; portable build cancelled.' }
    }
    $buildArgs = @('scripts/package_winui.py', '--output', $outputPath)
    if ($runtimePath) { $buildArgs += @('--reuse-runtime', $runtimePath) }
    if ($NoRestore) { $buildArgs += '--no-restore' }
    & $Python @buildArgs
    if ($LASTEXITCODE -ne 0) { throw 'WinUI portable build failed.' }
} finally {
    Pop-Location
}
