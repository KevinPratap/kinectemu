<#
.SYNOPSIS
  Hands-free test: run Kinect Adventures in the Xenia fork on a source that needs no camera or player.

.DESCRIPTION
  Wraps tools\phase0\skeleton_harness.py. It starts Xenia and a scripted or
  recorded source, takes a screenshot every second, grades the title's logs
  and prints PASS/FAIL per check. Output goes to -Out (screenshots, logs, sheet.jpg).
  The exit code is the harness's: 0 = PASS, 1 = FAIL.

.PARAMETER Case
  synthetic (scripted figure; default), cursor (hand-cursor check),
  or frames:<folder> (replay recorded webcam frames, for example a folder made
  with -Record in run_live.ps1).

.PARAMETER Seconds
  Run length. Default 420 for synthetic, 170 for cursor, 240 for frames.

.PARAMETER Iso
  Path to your Kinect Adventures image. Default: the harness's own default
  (E:\Kinect Adventures!\Kinect Adventures!.iso), which is the project
  owner's path. Pass your own.

.PARAMETER Xenia
  Path to xenia_canary.exe. Default: the fork build under external\.

.PARAMETER Out
  Output folder. Default: runs\harness_<case>_<timestamp>.

.PARAMETER DryRun
  Print the command, run nothing.

.EXAMPLE
  .\scripts\run_harness.ps1 -Iso "D:\Games\Kinect Adventures!.iso"
#>
[CmdletBinding()]
param(
    [string]$Case = "synthetic",
    [int]$Seconds = 0,
    [string]$Iso,
    [string]$Xenia,
    [string]$Out,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPy = Join-Path $Root "tools\.venv\Scripts\python.exe"
$Harness = Join-Path $Root "tools\phase0\skeleton_harness.py"

function Fail([string]$message) {
    Write-Host $message -ForegroundColor Red
    exit 1
}

# --- case and seconds --------------------------------------------------------
$caseName = $Case
$caseArg = $Case
if ($Case -eq "synthetic" -or $Case -eq "cursor") {
    if ($Seconds -le 0) {
        if ($Case -eq "cursor") { $Seconds = 170 } else { $Seconds = 420 }
    }
} elseif ($Case -like "frames:*") {
    $caseName = "frames"
    $folder = $Case.Substring(7)
    if (-not $folder) {
        Fail "Case frames:<folder> needs a folder, for example frames:fixtures\live_20261104_120000"
    }
    # The harness runs its frame source from tools\phase0, so give it an absolute path.
    if (-not [System.IO.Path]::IsPathRooted($folder)) { $folder = Join-Path $Root $folder }
    if (-not (Test-Path -LiteralPath $folder -PathType Container) -and -not $DryRun) {
        Fail "Frames folder not found: $folder"
    }
    $caseArg = "frames:$folder"
    if ($Seconds -le 0) { $Seconds = 240 }
} else {
    Fail "Unknown -Case '$Case'. Use synthetic, cursor, or frames:<folder>."
}

if (-not $Out) {
    $Out = Join-Path $Root ("runs\harness_{0}_{1}" -f $caseName, (Get-Date -Format "yyyyMMdd_HHmmss"))
} elseif (-not [System.IO.Path]::IsPathRooted($Out)) {
    $Out = Join-Path $Root $Out
}

# --- command -----------------------------------------------------------------
$pyArgs = @($Harness, "--case", $caseArg, "--seconds", "$Seconds", "--out", $Out)
if ($Iso) { $pyArgs += @("--iso", $Iso) }
if ($Xenia) { $pyArgs += @("--xenia", $Xenia) }

function Quote-Arg([string]$a) {
    if ($a -match "[\s!]") { return "`"$a`"" } else { return $a }
}
Write-Host "Working directory: $Root"
Write-Host ("`"$VenvPy`" " + (($pyArgs | ForEach-Object { Quote-Arg $_ }) -join " "))

if ($DryRun) {
    Write-Host "(dry run: nothing started)"
    exit 0
}

if (-not (Test-Path -LiteralPath $VenvPy -PathType Leaf)) {
    Fail "Python venv not found: $VenvPy. Run scripts\setup.ps1."
}
$isoCheck = $Iso
if (-not $isoCheck) { $isoCheck = "E:\Kinect Adventures!\Kinect Adventures!.iso" }
if (-not (Test-Path -LiteralPath $isoCheck -PathType Leaf)) {
    Fail "Game image not found: $isoCheck. Pass your own with -Iso."
}
$xeniaCheck = $Xenia
if (-not $xeniaCheck) {
    $xeniaCheck = Join-Path $Root "external\ryantimjohn-xenia\build\bin\Windows\Release\xenia_canary.exe"
}
if (-not (Test-Path -LiteralPath $xeniaCheck -PathType Leaf)) {
    Fail "Xenia fork build not found: $xeniaCheck. See docs\BUILDING.md."
}

Write-Host "Running for about $Seconds s plus start-up. The harness prints its grade at the end."
Push-Location -LiteralPath $Root
try {
    # Python writes progress to stderr; do not let Windows PowerShell 5.1 turn that into an error.
    $ErrorActionPreference = "Continue"
    $lines = & $VenvPy @pyArgs 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
} finally {
    Pop-Location
}
Write-Host "---- harness output (last 30 lines) ----"
$lines | Select-Object -Last 30 | ForEach-Object { Write-Host $_ }
Write-Host "----"
if ($code -eq 0) { Write-Host "Harness: PASS" } else { Write-Host "Harness: FAIL (exit code $code)" }
Write-Host "Output folder: $Out"
exit $code
