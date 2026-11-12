<#
.SYNOPSIS
  Play a Kinect title in the Xenia fork with the laptop webcam as the sensor.

.DESCRIPTION
  Checks that everything needed exists, starts xenia_canary.exe with the pipe
  depth backend and skeleton injection, waits about 3 s, then runs
  tools\phase0\synth_depth.py in the foreground (webcam -> depth + skeleton
  -> two named pipes). When Python ends, Xenia is stopped if still running.
  Press q in the depth window or Ctrl+C to stop.

  The webcam is opened by synth_depth.py. Frames stay on this machine.

.PARAMETER Iso
  Path to an image of your own Kinect Adventures disc (required).

.PARAMETER Xenia
  Path to xenia_canary.exe. Default: the fork build under external\.

.PARAMETER Camera
  Webcam index passed to synth_depth.py --camera.

.PARAMETER PushMm
  Move the player this far back in the synthetic scene (--push-mm).

.PARAMETER GainX
  Exaggerate sideways movement (--gain-x).

.PARAMETER Pipe
  Depth pipe name (xenia --nui_pipe, synth_depth --pipe).

.PARAMETER SkeletonPipe
  Skeleton pipe name (xenia --nui_skeleton_inject=pipe:<name>).

.PARAMETER Record
  Also save every camera frame to fixtures\live_<yyyyMMdd_HHmmss> (opt-in).
  fixtures\ is git-ignored. Do not commit or share these frames.

.PARAMETER ExtraSynthArgs
  Extra arguments for synth_depth.py, as one string, for example
  "--no-mirror --max-lateral-mm 400".

.PARAMETER DryRun
  Print the checks and the two commands, run nothing.

.EXAMPLE
  .\scripts\run_live.ps1 -Iso "D:\Games\Kinect Adventures!.iso"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Iso,
    [string]$Xenia,
    [int]$Camera = 1,
    [int]$PushMm = 700,
    [double]$GainX = 1.4,
    [string]$Pipe = "kinectemu",
    [string]$SkeletonPipe = "kinectemu-skel",
    [switch]$Record,
    [string]$ExtraSynthArgs = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Xenia) {
    $Xenia = Join-Path $Root "external\ryantimjohn-xenia\build\bin\Windows\Release\xenia_canary.exe"
}
$VenvPy = Join-Path $Root "tools\.venv\Scripts\python.exe"
$Models = Join-Path $Root "models"
$Intrinsics = Join-Path $Root "docs\data\intrinsics.json"
$SynthDir = Join-Path $Root "tools\phase0"

# --- checks ------------------------------------------------------------------
$missing = @()
if (-not (Test-Path -LiteralPath $Iso -PathType Leaf)) {
    $missing += "Game image not found: $Iso`n    Dump your own disc and pass its path with -Iso. This project does not provide game files."
}
if (-not (Test-Path -LiteralPath $Xenia -PathType Leaf)) {
    $missing += "Xenia fork build not found: $Xenia`n    Build it as described in docs\BUILDING.md, or pass -Xenia <path to xenia_canary.exe>."
}
if (-not (Test-Path -LiteralPath $VenvPy -PathType Leaf)) {
    $missing += "Python venv not found: $VenvPy`n    Run scripts\setup.ps1."
}
if (-not (Test-Path -LiteralPath $Models -PathType Container)) {
    $missing += "Models folder not found: $Models`n    Run scripts\setup.ps1 (it runs tools\fetch_models.py)."
}
if (-not (Test-Path -LiteralPath $Intrinsics -PathType Leaf)) {
    $missing += "Camera intrinsics not found: $Intrinsics`n    The file in the repo is calibrated for one laptop's camera. Calibrate yours with tools\phase0\calibrate_intrinsics.py (see docs\STATUS.md), or restore the file from git."
}
if ($missing.Count -gt 0) {
    Write-Host "Cannot start. Missing:" -ForegroundColor Red
    foreach ($m in $missing) { Write-Host ("  - " + $m) -ForegroundColor Red }
    exit 1
}

# --- commands ----------------------------------------------------------------
$xeniaArgs = @(
    $Iso,
    "--nui=pipe",
    "--nui_pipe=$Pipe",
    "--nui_skeleton_inject=pipe:$SkeletonPipe",
    "--nui_tilt_override=0"
)

$synthArgs = @(
    "synth_depth.py",
    "--models", "..\..\models",
    "--live",
    "--camera", "$Camera",
    "--intrinsics", "..\..\docs\data\intrinsics.json",
    "--push-mm", "$PushMm",
    "--gain-x", "$GainX",
    "--pipe", $Pipe,
    "--skeleton-pipe", $SkeletonPipe
)
if ($Record) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $synthArgs += @("--record-frames", "..\..\fixtures\live_$stamp")
}
if ($ExtraSynthArgs.Trim()) {
    $synthArgs += ($ExtraSynthArgs.Trim() -split "\s+")
}

function Quote-Arg([string]$a) {
    if ($a -match "[\s!]") { return "`"$a`"" } else { return $a }
}
Write-Host "1) Xenia:"
Write-Host ("   `"$Xenia`" " + (($xeniaArgs | ForEach-Object { Quote-Arg $_ }) -join " "))
Write-Host "2) Webcam pipeline (working directory: $SynthDir):"
Write-Host ("   `"$VenvPy`" " + (($synthArgs | ForEach-Object { Quote-Arg $_ }) -join " "))
if ($Record) {
    Write-Host "   Recording is ON: camera frames are saved under fixtures\ (git-ignored). Do not commit them."
}

if ($DryRun) {
    Write-Host "(dry run: nothing started)"
    exit 0
}

# --- run ---------------------------------------------------------------------
Write-Host ""
Write-Host "Stand about 2.2 m back with your whole body in view. Press q in the depth window to stop."

# Start-Process joins an argument array without quoting, so quote each element.
$xeniaProc = Start-Process -FilePath $Xenia -ArgumentList ($xeniaArgs | ForEach-Object { Quote-Arg $_ }) `
    -WorkingDirectory (Split-Path -Parent $Xenia) -PassThru
try {
    Start-Sleep -Seconds 3
    if ($xeniaProc.HasExited) {
        throw "Xenia exited right away (exit code $($xeniaProc.ExitCode)). Run it by hand to see why."
    }
    Push-Location -LiteralPath $SynthDir
    try {
        & $VenvPy @synthArgs
        Write-Host "synth_depth.py ended with exit code $LASTEXITCODE"
    } finally {
        Pop-Location
    }
} finally {
    if ($xeniaProc -and -not $xeniaProc.HasExited) {
        Write-Host "Stopping Xenia."
        Stop-Process -Id $xeniaProc.Id -Force -ErrorAction SilentlyContinue
    }
}
