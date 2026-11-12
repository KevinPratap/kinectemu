<#
.SYNOPSIS
  Create the Python environment and download the models for KinectEmu.

.DESCRIPTION
  1. Creates tools\.venv with Python 3.11 (py -3.11, else python) if missing.
  2. Upgrades pip, installs tools\requirements.txt.
  3. Installs rtmlib 0.0.16 with --no-deps, unless requirements.txt already lists it.
  4. Runs tools\fetch_models.py (downloads models into models\ and checks their hashes).

  It does not touch the camera, the emulator or your game files.

.PARAMETER DryRun
  Print every command and run nothing.

.PARAMETER SkipModels
  Do not run tools\fetch_models.py.

.EXAMPLE
  .\scripts\setup.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$SkipModels
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Venv = Join-Path $Root "tools\.venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$Req = Join-Path $Root "tools\requirements.txt"
$Fetch = Join-Path $Root "tools\fetch_models.py"

function Fail([string]$message) {
    Write-Host $message -ForegroundColor Red
    exit 1
}

# Run an external command and stop on a non-zero exit code.
function Invoke-Step([string]$exe, [string[]]$arguments) {
    $shown = $arguments | ForEach-Object { if ($_ -match "\s") { "`"$_`"" } else { $_ } }
    Write-Host ("> `"$exe`" " + ($shown -join " "))
    if ($DryRun) { return }
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) {
        Fail "Command failed with exit code ${LASTEXITCODE}."
    }
}

# True if "<exe> <args>" prints a Python 3.11.x version.
function Test-Python311([string]$exe, [string[]]$arguments) {
    try {
        $out = & $exe @arguments --version 2>&1 | Out-String
        return ($out -match "Python 3\.11\.")
    } catch {
        return $false
    }
}

# Find Python 3.11: the py launcher first, then python on PATH.
function Find-Python311 {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py -and (Test-Python311 $py.Source @("-3.11"))) {
        return @{ Exe = $py.Source; Args = @("-3.11") }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and (Test-Python311 $python.Source @())) {
        return @{ Exe = $python.Source; Args = @() }
    }
    return $null
}

Write-Host "KinectEmu setup"
Write-Host "Repo root: $Root"
if ($DryRun) { Write-Host "(dry run: nothing is executed)" }

# --- 1. venv -----------------------------------------------------------------
if (Test-Path -LiteralPath $VenvPy) {
    Write-Host "venv exists: $Venv"
} else {
    $found = Find-Python311
    if (-not $found) {
        Fail ("Python 3.11 was not found (tried 'py -3.11' and 'python').`n" +
              "Install it (winget install Python.Python.3.11) and run this script again.`n" +
              "Other versions are not supported: the pinned wheels are tested on 3.11 only.")
    }
    Invoke-Step $found.Exe ($found.Args + @("-m", "venv", $Venv))
}

# --- 2. pip and requirements -------------------------------------------------
if (-not (Test-Path -LiteralPath $Req)) {
    if ($DryRun) {
        Write-Host "note: $Req does not exist yet; a real run would stop here."
    } else {
        Fail "Missing $Req. Is this a complete checkout?"
    }
}
Invoke-Step $VenvPy @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Step $VenvPy @("-m", "pip", "install", "-r", $Req)

# --- 3. rtmlib (must not pull its own dependencies) --------------------------
$reqText = ""
if (Test-Path -LiteralPath $Req) { $reqText = Get-Content -LiteralPath $Req -Raw }
if ($reqText -match "(?im)^\s*rtmlib\b") {
    Write-Host "rtmlib is listed in requirements.txt; not installing it separately."
} else {
    Invoke-Step $VenvPy @("-m", "pip", "install", "--no-deps", "rtmlib==0.0.16")
}

# --- 4. models ---------------------------------------------------------------
if ($SkipModels) {
    Write-Host "Skipping model download (-SkipModels). Run tools\fetch_models.py later."
} else {
    if (-not (Test-Path -LiteralPath $Fetch) -and -not $DryRun) {
        Fail "Missing $Fetch. Is this a complete checkout?"
    }
    Invoke-Step $VenvPy @($Fetch)
}

Write-Host ""
if ($DryRun) {
    Write-Host "Dry run finished."
} else {
    Write-Host "Setup finished. Next: build the Xenia fork (docs\BUILDING.md), then scripts\run_live.ps1."
}
