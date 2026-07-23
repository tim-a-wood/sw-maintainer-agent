$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$installRoot = Join-Path $env:LOCALAPPDATA "Programs\Maintain"
$venvRoot = Join-Path $installRoot "venv"
$launcherPath = Join-Path $installRoot "Maintain.cmd"
$iconPath = Join-Path $installRoot "maintain.ico"
$repoRoot = Split-Path -Parent $PSScriptRoot
$iconSource = Join-Path $repoRoot "assets\maintain.ico.b64"
$packageSource = "sw-maintainer-agent[browser] @ https://github.com/tim-a-wood/sw-maintainer-agent/archive/refs/heads/main.zip"

function Find-Python {
    if (Get-Command "py.exe" -ErrorAction SilentlyContinue) {
        return @{ Command = "py.exe"; Prefix = @("-3") }
    }
    if (Get-Command "python.exe" -ErrorAction SilentlyContinue) {
        return @{ Command = "python.exe"; Prefix = @() }
    }
    throw "Python 3.11 or later is required. Install Python from https://www.python.org/downloads/windows/ and run this installer again."
}

function New-MaintainShortcut {
    param([string]$Path)
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $venvPython
    $shortcut.Arguments = "-m maintain"
    $shortcut.WorkingDirectory = $env:USERPROFILE
    $shortcut.IconLocation = "$iconPath,0"
    $shortcut.Description = "Maintain software with a verified AI workflow"
    $shortcut.Save()
}

function Assert-NativeCommand {
    param([string]$Action)
    if ($LASTEXITCODE -ne 0) {
        throw "$Action failed with exit code $LASTEXITCODE."
    }
}

function Add-UserPath {
    param([string]$Directory)
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($current -split ";" | Where-Object { $_ })
    if ($parts -notcontains $Directory) {
        $updated = (@($parts) + $Directory) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $updated, "User")
    }
}

function Try-PinTaskbar {
    param([string]$ShortcutPath)
    try {
        $shell = New-Object -ComObject Shell.Application
        $folder = $shell.Namespace((Split-Path -Parent $ShortcutPath))
        $item = $folder.ParseName((Split-Path -Leaf $ShortcutPath))
        if ($null -eq $item) {
            return $false
        }
        $item.InvokeVerb("taskbarpin")
        Start-Sleep -Milliseconds 800
        $pinned = Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\Maintain.lnk"
        return Test-Path $pinned
    }
    catch {
        return $false
    }
}

Write-Host ""
Write-Host "{ MAINTAIN }  INSTALL OR UPDATE" -ForegroundColor Green
Write-Host ""

$python = Find-Python
$pythonCommand = $python.Command
$pythonPrefix = $python.Prefix
& $pythonCommand @pythonPrefix -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or later is required."
}

New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
if (-not (Test-Path (Join-Path $venvRoot "Scripts\python.exe"))) {
    Write-Host "Creating the private Maintain environment..."
    & $pythonCommand @pythonPrefix -m venv $venvRoot
    Assert-NativeCommand -Action "Creating the Python environment"
}

$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$venvMaintain = Join-Path $venvRoot "Scripts\maintain.exe"

Write-Host "Installing the latest Maintain CLI..."
& $venvPython -m pip install --disable-pip-version-check --upgrade pip
Assert-NativeCommand -Action "Updating pip"
& $venvPython -m pip install --disable-pip-version-check --upgrade $packageSource
if ($LASTEXITCODE -ne 0) {
    Write-Host "The online update was unavailable. Installing from this folder..." -ForegroundColor Yellow
    Push-Location $repoRoot
    try {
        & $venvPython -m pip install --disable-pip-version-check --upgrade ".[browser]"
        Assert-NativeCommand -Action "Installing Maintain from the local folder"
    }
    finally {
        Pop-Location
    }
}

Write-Host "Preparing the browser used by Copilot and ChatGPT..."
& $venvPython -m playwright install chromium
Assert-NativeCommand -Action "Installing Chromium"

if (-not (Test-Path $iconSource)) {
    throw "The Maintain icon is missing from the installer package: $iconSource"
}
[IO.File]::WriteAllBytes(
    $iconPath,
    [Convert]::FromBase64String((Get-Content $iconSource -Raw).Trim())
)

$launcher = @"
@echo off
title Maintain
"$venvMaintain" %*
set "MAINTAIN_EXIT=%ERRORLEVEL%"
exit /b %MAINTAIN_EXIT%
"@
Set-Content -Path $launcherPath -Value $launcher -Encoding Ascii
Add-UserPath $installRoot

$desktop = [Environment]::GetFolderPath("Desktop")
$desktopShortcut = Join-Path $desktop "Maintain.lnk"
New-MaintainShortcut -Path $desktopShortcut

$startMenuFolder = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Maintain"
New-Item -ItemType Directory -Force -Path $startMenuFolder | Out-Null
$startMenuShortcut = Join-Path $startMenuFolder "Maintain.lnk"
New-MaintainShortcut -Path $startMenuShortcut

$pinned = Try-PinTaskbar -ShortcutPath $startMenuShortcut

Write-Host ""
Write-Host "Installed: $installRoot" -ForegroundColor Green
Write-Host "Desktop shortcut: $desktopShortcut" -ForegroundColor Green
if ($pinned) {
    Write-Host "Taskbar shortcut: pinned" -ForegroundColor Green
}
else {
    Write-Host "Windows did not allow automatic taskbar pinning." -ForegroundColor Yellow
    Write-Host "Right-click the Maintain desktop shortcut and choose 'Pin to taskbar'." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "New terminals can run: maintain"
Write-Host "The shortcut opens the last project you used."
