$ErrorActionPreference = "Continue"

# Reports which Maintain a new terminal would run, and why. Use this when the
# version shown is not the version you expected.

$installRoot = Join-Path $env:LOCALAPPDATA "Programs\Maintain"
$venvPython = Join-Path $installRoot "venv\Scripts\python.exe"
$repoRoot = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "{ MAINTAIN }  WHICH VERSION AM I RUNNING?" -ForegroundColor Green
Write-Host ""

Write-Host "This clone" -ForegroundColor Cyan
Write-Host "  Folder:  $repoRoot"
$git = Get-Command "git.exe" -ErrorAction SilentlyContinue
if ($null -ne $git -and (Test-Path (Join-Path $repoRoot ".git"))) {
    $branch = (& $git.Source -C $repoRoot rev-parse --abbrev-ref HEAD 2>$null | Out-String).Trim()
    $commit = (& $git.Source -C $repoRoot rev-parse --short HEAD 2>$null | Out-String).Trim()
    Write-Host "  Branch:  $branch at $commit"
    if ($branch -eq "main") {
        Write-Host "  WARNING: 'main' still holds the 0.9 agent. The new version lives on" -ForegroundColor Yellow
        Write-Host "           'simple-maintain'. Run: git checkout simple-maintain" -ForegroundColor Yellow
    }
}
else {
    Write-Host "  Branch:  (not a Git clone)"
}
$projectFile = Join-Path $repoRoot "pyproject.toml"
if (Test-Path -LiteralPath $projectFile) {
    $text = Get-Content -LiteralPath $projectFile -Raw
    $name = [regex]::Match($text, '(?m)^name\s*=\s*"(?<v>[^"]+)"').Groups["v"].Value
    $version = [regex]::Match($text, '(?m)^version\s*=\s*"(?<v>[^"]+)"').Groups["v"].Value
    Write-Host "  Source:  $name $version"
}

Write-Host ""
Write-Host "Installed runtime" -ForegroundColor Cyan
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $reported = (& $venvPython -m maintain --version 2>&1 | Out-String).Trim()
    Write-Host "  $installRoot"
    Write-Host "  Reports: $reported"
    $legacy = (& $venvPython -m pip show sw-maintainer-agent 2>$null | Out-String).Trim()
    if ($legacy) {
        Write-Host "  WARNING: the 0.9 distribution is still installed here." -ForegroundColor Yellow
        Write-Host "           Run install-or-update-windows.cmd to rebuild it." -ForegroundColor Yellow
    }
}
else {
    Write-Host "  Not installed. Run install-or-update-windows.cmd." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "What a NEW terminal runs" -ForegroundColor Cyan
$effectivePath = @(
    [Environment]::GetEnvironmentVariable("Path", "Machine"),
    [Environment]::GetEnvironmentVariable("Path", "User")
) -join ";"
$found = @()
foreach ($directory in ($effectivePath -split ";" | Where-Object { $_ })) {
    foreach ($leaf in @("maintain.cmd", "maintain.exe", "maintain.bat")) {
        $candidate = Join-Path $directory.Trim() $leaf
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $found += $candidate
        }
    }
}
if ($found.Count -eq 0) {
    Write-Host "  Nothing named 'maintain' is on PATH." -ForegroundColor Yellow
}
else {
    for ($i = 0; $i -lt $found.Count; $i++) {
        $marker = if ($i -eq 0) { "->" } else { "  " }
        $colour = if ($i -eq 0) {
            if ((Split-Path -Parent $found[$i]) -eq $installRoot) { "Green" } else { "Red" }
        } else { "Yellow" }
        Write-Host "  $marker $($found[$i])" -ForegroundColor $colour
    }
    if ((Split-Path -Parent $found[0]) -ne $installRoot) {
        Write-Host ""
        Write-Host "  The first entry wins, and it is not the one the installer wrote." -ForegroundColor Red
        Write-Host "  Remove it with:  py -3 -m pip uninstall -y sw-maintainer-agent maintain" -ForegroundColor Yellow
        Write-Host "  or delete that file, then open a new terminal." -ForegroundColor Yellow
    }
}
Write-Host ""
