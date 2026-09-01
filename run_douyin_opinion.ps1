# Copyright (c) 2025 relakkes@gmail.com
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1.
# Local fork helper for a small, authorized, date-bounded Douyin review.

[CmdletBinding()]
param(
    [string]$OpinionDate = (Get-Date).ToString("yyyy-MM-dd"),
    [string]$Keywords = (([char]0x897F).ToString() + ([char]0x9676)),
    [ValidateSet("all", "any")]
    [string]$Match = "all",
    [string]$OutputPath = "",
    [ValidateRange(10, 1000)]
    [int]$MaxVideos = 100,
    [ValidateRange(1, 5000)]
    [int]$MaxCommentsPerVideo = 200,
    [string]$WatchAccountsPath = "",
    [ValidateRange(1, 100)]
    [int]$OcrMaxImagesPerPost = 35,
    [ValidateRange(1, 500)]
    [int]$MaxWatchPostsPerAccount = 36,
    [switch]$IncludeReplies,
    [switch]$DisableImageOcr,
    [switch]$UseExistingChrome,
    [switch]$PromptKeywords,
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$outputPathWasSpecified = $PSBoundParameters.ContainsKey("OutputPath")

if ($PromptKeywords) {
    Add-Type -AssemblyName Microsoft.VisualBasic
    $dialogTitle = -join @(
        [char]0x6296,
        [char]0x97F3,
        [char]0x8206,
        [char]0x60C5,
        [char]0x76D1,
        [char]0x6D4B
    )
    $dialogMessage = -join @(
        [char]0x8BF7,
        [char]0x8F93,
        [char]0x5165,
        [char]0x641C,
        [char]0x7D22,
        [char]0x5173,
        [char]0x952E,
        [char]0x8BCD,
        [char]0xFF0C,
        [char]0x591A,
        [char]0x4E2A,
        [char]0x5173,
        [char]0x952E,
        [char]0x8BCD,
        [char]0x53EF,
        [char]0x7528,
        [char]0x7A7A,
        [char]0x683C,
        [char]0x6216,
        [char]0x9017,
        [char]0x53F7,
        [char]0x5206,
        [char]0x9694
    )
    $cancelMessage = -join @(
        [char]0x672A,
        [char]0x8F93,
        [char]0x5165,
        [char]0x5173,
        [char]0x952E,
        [char]0x8BCD,
        [char]0xFF0C,
        [char]0x5DF2,
        [char]0x53D6,
        [char]0x6D88,
        [char]0x8FD0,
        [char]0x884C
    )
    $enteredKeywords = [Environment]::GetEnvironmentVariable("MEDIACRAWLER_PROMPT_KEYWORDS")
    if ($null -eq $enteredKeywords) {
        $enteredKeywords = [Microsoft.VisualBasic.Interaction]::InputBox(
            $dialogMessage,
            $dialogTitle,
            $Keywords
        )
    }
    if ([string]::IsNullOrWhiteSpace($enteredKeywords)) {
        [void][Microsoft.VisualBasic.Interaction]::MsgBox(
            $cancelMessage,
            [Microsoft.VisualBasic.MsgBoxStyle]::Information,
            $dialogTitle
        )
        Write-Host "No keywords entered. Run cancelled."
        return
    }
    $Keywords = $enteredKeywords
}

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

if (-not $WatchAccountsPath) {
    $WatchAccountsPath = Join-Path $PSScriptRoot "douyin_watch_accounts.txt"
}
elseif (-not [IO.Path]::IsPathRooted($WatchAccountsPath)) {
    $WatchAccountsPath = Join-Path $PSScriptRoot $WatchAccountsPath
}
if (-not (Test-Path -LiteralPath $WatchAccountsPath -PathType Leaf)) {
    throw "Watch account file was not found: $WatchAccountsPath"
}
$watchAccounts = @(
    Get-Content -LiteralPath $WatchAccountsPath |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith("#") }
)
$watchAccountArgument = $watchAccounts -join ","

$Keywords = $Keywords.Replace([char]0xFF0C, ",")
$Keywords = $Keywords.Replace([char]0x3001, ",")
$Keywords = $Keywords.Replace([char]0xFF1B, ",")
$Keywords = $Keywords.Replace(";", ",")
$normalizedKeywords = (($Keywords -split "[,\s]+") | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join ","
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

if ($PromptKeywords -and -not $outputPathWasSpecified -and (Test-Path -LiteralPath $OutputPath)) {
    $outputDirectory = [IO.Path]::GetDirectoryName($OutputPath)
    $outputBaseName = [IO.Path]::GetFileNameWithoutExtension($OutputPath)
    $safeKeywords = ($normalizedKeywords -replace "[^\p{L}\p{N}_-]+", "_").Trim("_")
    if ($safeKeywords.Length -gt 40) {
        $safeKeywords = $safeKeywords.Substring(0, 40).Trim("_")
    }
    if (-not $safeKeywords) {
        $safeKeywords = "keywords"
    }

    $OutputPath = Join-Path $outputDirectory ("{0}-{1}.xlsx" -f $outputBaseName, $safeKeywords)
    if (Test-Path -LiteralPath $OutputPath) {
        $timeSuffix = (Get-Date).ToString("HHmmss")
        $OutputPath = Join-Path $outputDirectory ("{0}-{1}-{2}.xlsx" -f $outputBaseName, $safeKeywords, $timeSuffix)
    }
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
$ocrOption = if ($DisableImageOcr) { "no" } else { "yes" }

if ($CheckOnly) {
    Write-Host "Preflight check passed."
    Write-Host "Node.js: $nodeVersionText"
    Write-Host "uv: $(& $uvCommand.Source --version)"
    Write-Host "Keywords: $normalizedKeywords"
    Write-Host "Watch accounts: $($watchAccounts.Count)"
    Write-Host "Image OCR: $ocrOption"
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
    "--opinion_watch_accounts", $watchAccountArgument,
    "--opinion_ocr", $ocrOption,
    "--opinion_ocr_max_images", $OcrMaxImagesPerPost.ToString(),
    "--opinion_watch_max_posts", $MaxWatchPostsPerAccount.ToString(),
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
