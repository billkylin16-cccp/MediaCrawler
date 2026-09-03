# Copyright (c) 2025 relakkes@gmail.com
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1.

[CmdletBinding()]
param(
    [string]$Version = "0.2.1-beta.1",
    [string]$InnoCompiler = "",
    [switch]$SkipInstaller
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$packagingRoot = Join-Path $repositoryRoot "packaging"
$runtimeStage = Join-Path $packagingRoot ".runtime"
$browserStage = Join-Path $runtimeStage "ms-playwright"
$distRoot = Join-Path $repositoryRoot "dist"
$outputRoot = Join-Path $packagingRoot "output"
$applicationDist = Join-Path $distRoot "DouyinOpinionMonitor"
$specPath = Join-Path $packagingRoot "pyinstaller\DouyinOpinionMonitor.spec"
$installerScript = Join-Path $PSScriptRoot "installer.iss"

if (-not (Test-Path -LiteralPath (Join-Path $repositoryRoot "pyproject.toml") -PathType Leaf)) {
    throw "Repository root validation failed: $repositoryRoot"
}

$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    throw "uv was not found. Install uv on the build machine only. End users do not need it."
}
Push-Location $repositoryRoot
try {
    & $uvCommand.Source sync --frozen --group build
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed with exit code $LASTEXITCODE"
    }

    $browserListing = & $uvCommand.Source run python -m playwright install --list
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to list Playwright browsers."
    }
    $browserSources = @(
        $browserListing |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -match "[\\/](chromium|ffmpeg|winldd)-[^\\/]+$" } |
            ForEach-Object { [IO.Path]::GetFullPath($_) } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Container }
    )
    if (-not ($browserSources | Where-Object { [IO.Path]::GetFileName($_) -like "chromium-*" })) {
        throw "Playwright Chromium was not found. Run: uv run python -m playwright install chromium"
    }

    if (Test-Path -LiteralPath $runtimeStage) {
        $resolvedRuntime = [IO.Path]::GetFullPath($runtimeStage)
        if (-not $resolvedRuntime.StartsWith([IO.Path]::GetFullPath($packagingRoot), [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean unexpected runtime staging path: $resolvedRuntime"
        }
        Remove-Item -LiteralPath $resolvedRuntime -Recurse -Force
    }
    New-Item -ItemType Directory -Path $browserStage -Force | Out-Null
    foreach ($source in $browserSources) {
        Copy-Item -LiteralPath $source -Destination $browserStage -Recurse -Force
    }

    $env:MEDIACRAWLER_BUILD_BROWSERS_PATH = $browserStage
    & $uvCommand.Source run --group build pyinstaller --noconfirm --clean $specPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $applicationDist "DouyinOpinionMonitor.exe") -PathType Leaf)) {
        throw "Packaged application executable was not created."
    }

    $selfTestData = Join-Path $packagingRoot ".self-test-data"
    $selfTestReport = Join-Path $packagingRoot ".self-test-report.json"
    $previousDataDir = $env:MEDIACRAWLER_DATA_DIR
    $env:MEDIACRAWLER_DATA_DIR = $selfTestData
    try {
        $selfTest = Start-Process `
            -FilePath (Join-Path $applicationDist "DouyinOpinionMonitor.exe") `
            -ArgumentList @("--self-test", "--self-test-report", $selfTestReport) `
            -WindowStyle Hidden `
            -Wait `
            -PassThru
        if ($selfTest.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $selfTestReport -PathType Leaf)) {
            throw "Packaged application self-test failed with exit code $($selfTest.ExitCode)."
        }
        $selfTestResult = Get-Content -LiteralPath $selfTestReport -Raw | ConvertFrom-Json
        if (-not $selfTestResult.ok) {
            throw "Packaged application self-test reported an error: $($selfTestResult.errors -join '; ')"
        }
    }
    finally {
        $env:MEDIACRAWLER_DATA_DIR = $previousDataDir
    }

    New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
    if ($SkipInstaller) {
        Write-Host "Application folder created: $applicationDist"
        return
    }

    if (-not $InnoCompiler) {
        $knownCompiler = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
        if (Test-Path -LiteralPath $knownCompiler -PathType Leaf) {
            $InnoCompiler = $knownCompiler
        }
        else {
            $compilerCommand = Get-Command iscc.exe -ErrorAction SilentlyContinue
            if ($compilerCommand) {
                $InnoCompiler = $compilerCommand.Source
            }
        }
    }
    if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler -PathType Leaf)) {
        throw "Inno Setup 6 compiler was not found. Supply -InnoCompiler or use -SkipInstaller."
    }

    & $InnoCompiler "/DAppVersion=$Version" "/DSourceDir=$applicationDist" "/DOutputDir=$outputRoot" $installerScript
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE"
    }
    $installerPath = Join-Path $outputRoot "DouyinOpinionMonitor-$Version-win-x64-setup.exe"
    if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
        throw "Installer was not created: $installerPath"
    }
    $hash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash
    Set-Content -LiteralPath "$installerPath.sha256" -Value "$hash  $([IO.Path]::GetFileName($installerPath))" -Encoding ascii
    Write-Host "Installer created: $installerPath"
    Write-Host "SHA256: $hash"
}
finally {
    Pop-Location
}
