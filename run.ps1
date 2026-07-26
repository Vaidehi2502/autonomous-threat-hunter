#!/usr/bin/env pwsh
# Launches the FastAPI backend and the Vite dashboard together for a demo (Windows).
# PowerShell counterpart to run.sh -- same ports, same behaviour.
#
# Usage:  .\run.ps1
# If PowerShell blocks the script, run once in the same terminal:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$ApiPort = 8000
$DashPort = 5173

# Use the venv if one exists, otherwise fall back to python on PATH.
$venvPython = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $python = $venvPython
    Write-Host "Using venv: venv\Scripts\python.exe"
} else {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "python not found on PATH. Install Python, or create a venv: python -m venv venv"
    }
    $python = $cmd.Source
    Write-Host "No venv found - using system python: $python"
}

# The API serves precomputed results; without this file it has nothing to serve.
if (-not (Test-Path (Join-Path $PSScriptRoot "outputs\threat_scores.parquet"))) {
    throw "outputs\threat_scores.parquet is missing. Run the pipeline (src/load_data.py -> features.py -> detect.py) or restore outputs/."
}

if (-not (Test-Path (Join-Path $PSScriptRoot "dashboard\node_modules"))) {
    Write-Host "Installing dashboard dependencies (first run only)..."
    Push-Location (Join-Path $PSScriptRoot "dashboard")
    npm install --no-audit --no-fund
    Pop-Location
}

$procs = @()
try {
    Write-Host "Starting API on port $ApiPort ..."
    $procs += Start-Process -FilePath $python `
        -ArgumentList "-m", "uvicorn", "src.api:app", "--port", "$ApiPort" `
        -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru

    Write-Host "Starting dashboard on port $DashPort ..."
    $procs += Start-Process -FilePath "npm.cmd" `
        -ArgumentList "run", "dev", "--", "--port", "$DashPort" `
        -WorkingDirectory (Join-Path $PSScriptRoot "dashboard") -NoNewWindow -PassThru

    Write-Host ""
    Write-Host "API:       http://localhost:$ApiPort"
    Write-Host "API docs:  http://localhost:$ApiPort/docs"
    Write-Host "Dashboard: http://localhost:$DashPort"
    Write-Host "Press Ctrl+C to stop both."
    Write-Host ""

    Wait-Process -Id ($procs | ForEach-Object { $_.Id })
}
finally {
    # /T kills the whole tree -- npm.cmd spawns node as a child.
    foreach ($p in $procs) {
        if ($p -and -not $p.HasExited) {
            & taskkill /T /F /PID $p.Id 2>$null | Out-Null
        }
    }
    Write-Host "Stopped."
}
