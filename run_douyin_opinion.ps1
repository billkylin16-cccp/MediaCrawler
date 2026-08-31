# Copyright (c) 2025 relakkes@gmail.com
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1.
# Local fork helper for a small, authorized, date-bounded Douyin review.

[CmdletBinding()]
param(
    [string]$OpinionDate = (Get-Date).ToString("yyyy-MM-dd"),
    [string]$Keywords = (([char]0x6B66).ToString() + ([char]0x965F) + "," + ([char]0x897F) + ([char]0x9676)),
    [ValidateSet("all", "any")]
    [string]$Match = "all",
    [string]$OutputPath = "",
    [ValidateRange(10, 1000)]
    [int]$MaxVideos = 100,
    [ValidateRange(1, 5000)]
    [int]$MaxCommentsPerVideo = 200,
    [switch]$IncludeReplies,
    [switch]$UseExistingChrome,
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

try {
    $parsedDate = [DateTime]::ParseExact(
        $OpinionDate,
        "yyyy-MM-dd",
        [Globalization.CultureInfo]::InvariantCulture
    )
}
catch {
    throw "OpinionDate must use YYYY-MM-DD, for example 2026-08-24."
}

$normalizedKeywords = ($Keywords.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join ","
if (-not $normalizedKeywords) {
    throw "Keywords must contain at least one value. Separate values with commas."
}

if (-not $OutputPath) {
    $reportSuffix = -join @(
        [char]0x6296,
        [char]0x97F3,
        [char]0x8206,
        [char]0x8BBA,
        [char]0x68C0,
        [char]0x6D4B
    )
    $fileName = "{0}.{1:D2}{2}.xlsx" -f $parsedDate.Month, $parsedDate.Day, $reportSuffix
    $OutputPath = Join-Path $PSScriptRoot $fileName
}
elseif (-not [IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $PSScriptRoot $OutputPath
}

if ([IO.Path]::GetExtension($OutputPath) -ne ".xlsx") {
    throw "OutputPath must end with .xlsx."
}

$uvCommand = Get-Command -Name "uv" -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    throw "uv was not found. Open a new PowerShell window and try again."
}

$nodeCommand = Get-Command -Name "node" -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
    throw "node was not found. Node.js 16 or newer is required."
}
$nodeVersionText = (& $nodeCommand.Source --version).TrimStart([char]"v")
try {
    $nodeVersion = [Version]$nodeVersionText
}
catch {
    throw "Unable to parse the Node.js version: $nodeVersionText"
}
if ($nodeVersion.Major -lt 16) {
    throw "Node.js $nodeVersionText is too old. Version 16 or newer is required."
}

$env:MEDIACRAWLER_ENABLE_CDP_MODE = if ($UseExistingChrome) { "true" } else { "false" }
$env:MEDIACRAWLER_CDP_CONNECT_EXISTING = if ($UseExistingChrome) { "true" } else { "false" }
$browserMode = if ($UseExistingChrome) { "existing Chrome (CDP)" } else { "Playwright Chromium" }

if ($CheckOnly) {
    Write-Host "Preflight check passed."
    Write-Host "Node.js: $nodeVersionText"
    Write-Host "uv: $(& $uvCommand.Source --version)"
    Write-Host "Keywords: $normalizedKeywords"
    Write-Host "Browser mode: $browserMode"
    Write-Host "Output: $OutputPath"
    return
}

if (Test-Path -LiteralPath $OutputPath) {
    throw "The report already exists and will not be overwritten: $OutputPath. Use -OutputPath with a new file name."
}

$replyOption = if ($IncludeReplies) { "yes" } else { "no" }
$arguments = @(
    "run", "main.py",
    "--platform", "dy",
    "--lt", "qrcode",
    "--type", "search",
    "--keywords", $normalizedKeywords,
    "--douyin_opinion_report", "yes",
    "--opinion_date", $OpinionDate,
    "--opinion_match", $Match,
    "--opinion_output", $OutputPath,
    "--get_comment", "yes",
    "--get_sub_comment", $replyOption,
    "--max_comments_count_singlenotes", $MaxCommentsPerVideo.ToString(),
    "--crawler_max_notes_count", $MaxVideos.ToString()
)

Push-Location $PSScriptRoot
try {
    & $uvCommand.Source @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "MediaCrawler failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Write-Host "Report created: $OutputPath"
