# Maintain — install or update, one script for both.
# Run it from the repository checkout. Run it again to update.

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

Write-Host "== Maintain setup =="

Write-Host "1. Python check"
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Error ("Python is absent. Install Python 3.11 or later from " +
                 "python.org. Then run this script again.")
}
$version = & py -3 -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$version -lt [version]"3.11") {
    Write-Error "Python $version is too old. Version 3.11 or later is required."
}
Write-Host "   PASS: Python $version"

Write-Host "2. pipx check"
& py -3 -m pip install --user --quiet --upgrade pipx
& py -3 -m pipx ensurepath *> $null
Write-Host "   PASS: pipx is ready"

Write-Host "3. Maintain install or update (ui + explain)"
& py -3 -m pipx install --force "$repo[ui,explain]"
if ($LASTEXITCODE -ne 0) {
    Write-Error "The Maintain install failed. Read the pipx output above."
}
Write-Host "   PASS: Maintain is installed"

Write-Host "4. ffmpeg check"
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ffmpeg) {
    Write-Host "   PASS: ffmpeg is present"
} else {
    Write-Host "   ffmpeg is absent. The script installs it with winget."
    winget install --id Gyan.FFmpeg -e --accept-source-agreements `
        --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Warning ("winget could not install ffmpeg. Install it by " +
                       "hand: winget install ffmpeg")
    }
}

Write-Host "5. Verification"
$manim = & py -3 -m pipx runpip maintain show manim 2>$null |
    Select-String "^Version:"
if ($manim) {
    Write-Host "   PASS: Manim $($manim.ToString().Split(' ')[1]) in the app environment"
} else {
    Write-Warning "Manim is not visible in the app environment."
}
Write-Host ""
Write-Host "Done. Start the app with: maintain-ui"
Write-Host "If the command is absent, open a new terminal first."
Write-Host "To update later, run this same script again."
