<#
.SYNOPSIS
    Publishes the API to a Hugging Face Docker Space.

.DESCRIPTION
    Assembles a minimal Space repository containing only what the API needs and
    pushes it. Deliberately does NOT push the whole project: outputs/ holds two
    pipeline intermediates above 14 MB, and the Hub rejects files over 10 MB
    that are not tracked with Git LFS. The API only ever reads
    threat_scores.parquet (3.7 MB), so the Space stays small and LFS-free.

.PARAMETER SpaceId
    Target Space, as <owner>/<name>. It must already exist -- create it at
    https://huggingface.co/new-space with SDK "Docker" and template "Blank".

.EXAMPLE
    .\deploy\huggingface\publish.ps1 -SpaceId Vaidehi2502/autonomous-threat-hunter

.NOTES
    Run `hf auth login` first, and answer yes when it offers to configure the
    git credential helper -- otherwise the push below will prompt for a
    password that a plain account password will not satisfy.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SpaceId,

    [string]$WorkDir = (Join-Path $env:TEMP "ath-hf-space"),

    [string]$Message = "Deploy Autonomous Threat Hunter API"
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Write-Host "Project root: $repoRoot"

# Fail early with a clear message rather than a confusing push error later.
& hf auth whoami *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Not logged in to Hugging Face. Run:  hf auth login"
}

$required = @(
    "Dockerfile",
    "requirements.txt",
    "src",
    "outputs\threat_scores.parquet",
    "deploy\huggingface\README.md"
)
foreach ($item in $required) {
    if (-not (Test-Path (Join-Path $repoRoot $item))) {
        throw "Missing required file: $item"
    }
}

if (Test-Path $WorkDir) { Remove-Item -Recurse -Force $WorkDir }

Write-Host "Cloning Space https://huggingface.co/spaces/$SpaceId ..."
git clone "https://huggingface.co/spaces/$SpaceId" $WorkDir
if ($LASTEXITCODE -ne 0) {
    throw "Clone failed. Create the Space first at https://huggingface.co/new-space (SDK: Docker, template: Blank)."
}

Write-Host "Assembling Space contents..."

# The Space card. Its YAML front matter is what tells the Hub to build a Docker
# Space and which port to route to, so it must land as README.md at the root.
Copy-Item (Join-Path $repoRoot "deploy\huggingface\README.md") (Join-Path $WorkDir "README.md") -Force
Copy-Item (Join-Path $repoRoot "Dockerfile") $WorkDir -Force
Copy-Item (Join-Path $repoRoot "requirements.txt") $WorkDir -Force

$srcDest = Join-Path $WorkDir "src"
if (Test-Path $srcDest) { Remove-Item -Recurse -Force $srcDest }
Copy-Item (Join-Path $repoRoot "src") $srcDest -Recurse -Force
Get-ChildItem -Path $srcDest -Include "__pycache__" -Recurse -Directory |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$outDir = Join-Path $WorkDir "outputs"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
Copy-Item (Join-Path $repoRoot "outputs\threat_scores.parquet") $outDir -Force

# The Dockerfile copies only threat_scores.parquet, but the Space build context
# is this directory -- keep anything oversized from sneaking in.
$oversized = Get-ChildItem -Path $WorkDir -Recurse -File |
    Where-Object { $_.Length -gt 10MB -and $_.FullName -notlike "*\.git\*" }
foreach ($f in $oversized) {
    Write-Warning "Over 10 MB, the Hub will require LFS: $($f.Name) ($([math]::Round($f.Length/1MB,1)) MB)"
}

Push-Location $WorkDir
try {
    git add -A
    $pending = git status --porcelain
    if (-not $pending) {
        Write-Host "Space already up to date; nothing to push."
        return
    }
    git commit -m $Message
    Write-Host "Pushing to Hugging Face..."
    git push
    if ($LASTEXITCODE -ne 0) { throw "Push failed. Check `hf auth login` and Space write permissions." }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Done. Build progress: https://huggingface.co/spaces/$SpaceId"
Write-Host "Once the Space is Running, the API base URL is:"
Write-Host "  https://$($SpaceId.Replace('/','-').ToLower()).hf.space"
Write-Host ""
Write-Host "Next: set VITE_API_BASE on the frontend to that URL, then set"
Write-Host "CORS_ALLOW_ORIGINS on the Space to the dashboard origin."
