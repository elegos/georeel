#Requires -Version 5.1
<#
.SYNOPSIS
    GeoReel installer for Windows.
.DESCRIPTION
    Downloads the latest GeoReel release wheel from GitHub and installs it.
    Optionally installs Python 3.14 and FFmpeg via winget if not already present.
.EXAMPLE
    irm https://raw.githubusercontent.com/elegos/georeel/main/scripts/install.ps1 | iex
    — or —
    .\scripts\install.ps1
#>

$ErrorActionPreference = "Stop"

$REPO          = "elegos/georeel"
$MIN_PY_MAJOR  = 3
$MIN_PY_MINOR  = 14

function Write-Info    { param($msg) Write-Host "[georeel] $msg" -ForegroundColor Cyan }
function Write-Success { param($msg) Write-Host "[georeel] $msg" -ForegroundColor Green }
function Write-Warn    { param($msg) Write-Host "[georeel] WARNING: $msg" -ForegroundColor Yellow }
function Write-Fail    { param($msg) Write-Host "[georeel] ERROR: $msg" -ForegroundColor Red; exit 1 }

Write-Info "GeoReel installer — Windows"
Write-Host ""

# ── Python 3.14 check ─────────────────────────────────────────────────
function Find-Python {
    $candidates = @("python3.14", "python3", "python")
    foreach ($cmd in $candidates) {
        $exe = Get-Command $cmd -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        try {
            $ver = & $exe.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            $parts = $ver.Split(".")
            $major = [int]$parts[0]; $minor = [int]$parts[1]
            if ($major -gt $MIN_PY_MAJOR -or ($major -eq $MIN_PY_MAJOR -and $minor -ge $MIN_PY_MINOR)) {
                return $exe.Source
            }
        } catch { continue }
    }
    return $null
}

$PYTHON = Find-Python
if (-not $PYTHON) {
    Write-Warn "Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR}+ not found."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Info "Installing Python 3.14 via winget…"
        winget install --id Python.Python.3.14 --silent --accept-package-agreements --accept-source-agreements
        # Refresh PATH
        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("PATH", "User")
        $PYTHON = Find-Python
        if (-not $PYTHON) {
            Write-Fail "Python 3.14 was installed but could not be found on PATH. Open a new terminal and try again."
        }
    } else {
        Write-Warn "winget is not available. Download Python 3.14 from:"
        Write-Warn "  https://www.python.org/downloads/"
        Write-Fail "Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR}+ is required."
    }
}

$pyVersion = & $PYTHON -c "import sys; print(sys.version)" 2>$null
Write-Success "Found Python: $pyVersion"

# ── FFmpeg check ──────────────────────────────────────────────────────
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warn "FFmpeg not found."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $resp = Read-Host "Install FFmpeg via winget? [Y/n]"
        if ($resp -eq "" -or $resp -match "^[Yy]") {
            Write-Info "Installing FFmpeg via winget…"
            winget install --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
            $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                        [System.Environment]::GetEnvironmentVariable("PATH", "User")
            if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
                Write-Success "FFmpeg installed."
            } else {
                Write-Warn "FFmpeg installed but not found on PATH yet. You may need to restart your terminal."
            }
        }
    } else {
        Write-Warn "Install FFmpeg from: https://ffmpeg.org/download.html"
        Write-Warn "Ensure ffmpeg.exe is on your PATH before running GeoReel."
    }
} else {
    $ffVer = (ffmpeg -version 2>&1 | Select-String "ffmpeg version" | Select-Object -First 1).Line
    Write-Success "FFmpeg found: $ffVer"
}

# ── Download latest release wheel ─────────────────────────────────────
Write-Info "Fetching latest release from GitHub…"

$headers = @{
    "Accept"     = "application/vnd.github+json"
    "User-Agent" = "georeel-installer"
}
try {
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$REPO/releases/latest" -Headers $headers
} catch {
    Write-Fail "Failed to reach GitHub API: $_"
}

$whlAsset = $release.assets | Where-Object { $_.name -like "*.whl" } | Select-Object -First 1
if (-not $whlAsset) {
    Write-Fail "No .whl asset found in the latest release."
}

$whlName = $whlAsset.name
$whlUrl  = $whlAsset.browser_download_url
Write-Info "Downloading $whlName…"

$tmpWhl = Join-Path $env:TEMP $whlName
try {
    Invoke-WebRequest -Uri $whlUrl -OutFile $tmpWhl -UseBasicParsing
} catch {
    Write-Fail "Download failed: $_"
}

# ── Install ───────────────────────────────────────────────────────────
Write-Info "Installing GeoReel…"
& $PYTHON -m pip install --upgrade $tmpWhl
Remove-Item -Path $tmpWhl -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Success "GeoReel installed successfully!"
Write-Host ""
Write-Info "Run it with:"
Write-Host "    georeel" -ForegroundColor White
Write-Host "  or:"
Write-Host "    python -m georeel.main" -ForegroundColor White
Write-Host ""
Write-Info "Blender (4.2 LTS, 4.4, or 4.5 LTS) is required for 3D rendering."
Write-Info "GeoReel can download it automatically from Options -> Blender..."
Write-Info "Or install it from: https://www.blender.org/download/"
