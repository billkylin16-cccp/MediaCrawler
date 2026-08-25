# Copyright (c) 2025 relakkes@gmail.com
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1.
# Local fork helper for a small, authorized, date-bounded Douyin review.

[CmdletBinding()]
param(
    [string]$OpinionDate = (Get-Date).ToString("yyyy-MM-dd"),
    [string]$Keywords = "武陟,西陶",
    [ValidateSet("all", "any")]
    [string]$Match = "all",
    [string]$OutputPath = "",
    [ValidateRange(10, 1000)]
    [int]$MaxVideos = 100,
    [ValidateRange(1, 5000)]
    [int]$MaxCommentsPerVideo = 200,
    [switch]$IncludeReplies
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
    throw "OpinionDate 必须为 YYYY-MM-DD，例如 2026-08-24。"
}

$normalizedKeywords = ($Keywords.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join ","
if (-not $normalizedKeywords) {
    throw "Keywords 至少需要一个关键词，多个关键词请用英文逗号分隔。"
}

if (-not $OutputPath) {
    $fileName = "{0}.{1:D2}抖音舆论检测.xlsx" -f $parsedDate.Month, $parsedDate.Day
    $OutputPath = Join-Path $PSScriptRoot $fileName
}
elseif (-not [IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $PSScriptRoot $OutputPath
}

if ([IO.Path]::GetExtension($OutputPath) -ne ".xlsx") {
    throw "OutputPath 必须以 .xlsx 结尾。"
}
if (Test-Path -LiteralPath $OutputPath) {
    throw "报表已存在，不会覆盖：$OutputPath。请先改名归档，或通过 -OutputPath 指定新文件。"
}

$uvCommand = Get-Command -Name "uv" -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    throw "未找到 uv。此命令行报表不需要 npm install；请先按 README 安装 uv，并在项目目录运行 uv sync。"
}

$nodeCommand = Get-Command -Name "node" -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
    throw "未找到 node。抖音签名代码需要 Node.js 16 或更高版本，但无需为此报表运行 npm install。"
}
$nodeVersionText = (& $nodeCommand.Source --version).TrimStart([char]"v")
try {
    $nodeVersion = [Version]$nodeVersionText
}
catch {
    throw "无法识别 Node.js 版本：$nodeVersionText"
}
if ($nodeVersion.Major -lt 16) {
    throw "Node.js 版本过低：$nodeVersionText；抖音功能需要 16 或更高版本。"
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
        throw "MediaCrawler 运行失败，退出码：$LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Write-Host "报表已生成：$OutputPath"
