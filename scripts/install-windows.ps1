$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$installRoot = Join-Path $env:LOCALAPPDATA "Programs\Maintain"
$venvRoot = Join-Path $installRoot "venv"
$launcherPath = Join-Path $installRoot "Maintain.cmd"
$shortcutLauncherPath = Join-Path $installRoot "Maintain-shortcut.cmd"
$runtimeLauncherPath = Join-Path $installRoot "launch-maintain.ps1"
$installLogPath = Join-Path $installRoot "install.log"
$iconPath = Join-Path $installRoot "maintain.ico"
$repoRoot = Split-Path -Parent $PSScriptRoot
$iconSource = Join-Path $repoRoot "assets\maintain.ico.b64"
$packageSource = "sw-maintainer-agent[browser] @ https://github.com/tim-a-wood/sw-maintainer-agent/archive/refs/heads/main.zip"
$packageSourceOverridden = $false
$packageSourceOverride = [Environment]::GetEnvironmentVariable("MAINTAIN_PACKAGE_SOURCE")

function Test-PythonCandidate {
    param(
        [string]$Command,
        [string[]]$Prefix
    )
    try {
        & $Command @Prefix -c "import sys, venv; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" `
            2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Find-Python {
    $candidates = @()
    $launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        $candidates += @{ Command = $launcher.Source; Prefix = @("-3") }
    }
    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        $candidates += @{ Command = $python.Source; Prefix = @() }
    }
    foreach ($candidate in $candidates) {
        if (Test-PythonCandidate -Command $candidate.Command -Prefix $candidate.Prefix) {
            return $candidate
        }
    }
    throw "Python 3.11 or later is required. Install Python from https://www.python.org/downloads/windows/ and run this installer again."
}

function Find-Git {
    $git = Get-Command "git.exe" -ErrorAction SilentlyContinue
    if ($null -eq $git) {
        throw "Git is required. Install Git for Windows from https://git-scm.com/download/win and run this installer again."
    }
    & $git.Source --version | Out-Host
    Assert-NativeCommand -Action "Checking Git"
    return $git.Source
}

function New-MaintainShortcut {
    param([string]$Path)
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $shortcutLauncherPath
    $shortcut.Arguments = ""
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

function Test-PrivateEnvironment {
    param([string]$PythonPath)
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return $false
    }
    try {
        & $PythonPath -c "import pip, sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" `
            2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
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

New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
$transcriptStarted = $false
try {
    try {
        Start-Transcript -LiteralPath $installLogPath -Append | Out-Null
        $transcriptStarted = $true
    }
    catch {
        Write-Warning "The install log could not be opened: $($_.Exception.Message)"
    }

    Write-Host ""
    Write-Host "{ MAINTAIN }  INSTALL OR UPDATE" -ForegroundColor Green
    Write-Host ""
    Write-Host "Install log: $installLogPath"

    if (-not [string]::IsNullOrWhiteSpace($packageSourceOverride)) {
        if ($packageSourceOverride.Contains("`r") -or $packageSourceOverride.Contains("`n") -or
                $packageSourceOverride.IndexOf([char]0) -ge 0) {
            throw "MAINTAIN_PACKAGE_SOURCE must be one single-line pip requirement."
        }
        $packageSource = $packageSourceOverride.Trim()
        if ($packageSource.StartsWith("-")) {
            throw "MAINTAIN_PACKAGE_SOURCE must be a requirement, not a pip option."
        }
        $packageSourceOverridden = $true
        Write-Host "Using the MAINTAIN_PACKAGE_SOURCE override."
    }

    $python = Find-Python
    $pythonCommand = $python.Command
    $pythonPrefix = $python.Prefix
    $gitCommand = Find-Git
    Write-Host "Python: $pythonCommand $($pythonPrefix -join ' ')"
    Write-Host "Git: $gitCommand"

    $venvPython = Join-Path $venvRoot "Scripts\python.exe"
    if ((Test-Path -LiteralPath $venvRoot) -and
            -not (Test-PrivateEnvironment -PythonPath $venvPython)) {
        if ((Split-Path -Parent $venvRoot) -ne $installRoot) {
            throw "Refusing to replace an unexpected Python environment path: $venvRoot"
        }
        Write-Host "The existing private environment is unusable. Recreating it..." -ForegroundColor Yellow
        Remove-Item -LiteralPath $venvRoot -Recurse -Force
    }
    if (-not (Test-PrivateEnvironment -PythonPath $venvPython)) {
        Write-Host "Creating the private Maintain environment..."
        & $pythonCommand @pythonPrefix -m venv $venvRoot
        Assert-NativeCommand -Action "Creating the Python environment"
        if (-not (Test-PrivateEnvironment -PythonPath $venvPython)) {
            throw "The private Maintain Python environment could not be started."
        }
    }

    Write-Host "Installing the latest Maintain CLI..."
    & $venvPython -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        Write-Host "pip could not update; continuing with the private environment's current pip." `
            -ForegroundColor Yellow
    }
    & $venvPython -m pip install --disable-pip-version-check --no-cache-dir `
        --upgrade --force-reinstall $packageSource
    if ($LASTEXITCODE -ne 0) {
        if ($packageSourceOverridden) {
            throw "Installing MAINTAIN_PACKAGE_SOURCE failed with exit code $LASTEXITCODE."
        }
        Write-Host "The online update was unavailable. Installing from this folder..." -ForegroundColor Yellow
        Push-Location $repoRoot
        try {
            & $venvPython -m pip install --disable-pip-version-check --no-cache-dir `
                --upgrade --force-reinstall ".[browser]"
            Assert-NativeCommand -Action "Installing Maintain from the local folder"
        }
        finally {
            Pop-Location
        }
    }

    $installedVersion = (& $venvPython -m maintain --version | Out-String).Trim()
    Assert-NativeCommand -Action "Checking the installed Maintain runtime"
    if (-not $installedVersion) {
        throw "The installed Maintain runtime did not report its version."
    }

    Write-Host "Preparing the browser used by Copilot and ChatGPT..."
    & $venvPython -m playwright install chromium
    Assert-NativeCommand -Action "Installing Chromium"

    if (-not (Test-Path -LiteralPath $iconSource)) {
        throw "The Maintain icon is missing from the installer package: $iconSource"
    }
    [IO.File]::WriteAllBytes(
        $iconPath,
        [Convert]::FromBase64String((Get-Content -LiteralPath $iconSource -Raw).Trim())
    )

    $runtimeLauncher = @'
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$MaintainArguments
)

$exitCode = 1
$transcriptStarted = $false
$installRoot = Split-Path -Parent $PSCommandPath
$venvPython = Join-Path $installRoot "venv\Scripts\python.exe"
$logRoot = Join-Path $env:USERPROFILE ".maintain\logs"
$runtimeLog = Join-Path $logRoot "maintain-runtime.log"

try {
    New-Item -ItemType Directory -Force -Path $logRoot -ErrorAction Stop | Out-Null
    try {
        Start-Transcript -LiteralPath $runtimeLog -Append -ErrorAction Stop | Out-Null
        $transcriptStarted = $true
    }
    catch {
        Write-Warning "Runtime logging is unavailable: $($_.Exception.Message)"
    }
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "The Maintain runtime is missing. Run install-or-update-windows.cmd again."
    }
    & $venvPython -m maintain @MaintainArguments
    $exitCode = $LASTEXITCODE
}
catch {
    Write-Host ("Maintain could not start: " + $_.Exception.Message) -ForegroundColor Red
    $exitCode = 1
}
finally {
    if ($transcriptStarted) {
        Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
    }
}

exit $exitCode
'@
    Set-Content -LiteralPath $runtimeLauncherPath -Value $runtimeLauncher -Encoding UTF8

    $launcher = @'
@echo off
setlocal
title Maintain
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-maintain.ps1" %*
set "MAINTAIN_EXIT=%ERRORLEVEL%"
exit /b %MAINTAIN_EXIT%
'@
    Set-Content -LiteralPath $launcherPath -Value $launcher -Encoding Ascii

    $shortcutLauncher = @'
@echo off
setlocal
call "%~dp0Maintain.cmd" %*
set "MAINTAIN_EXIT=%ERRORLEVEL%"
if not "%MAINTAIN_EXIT%"=="0" (
  echo.
  echo Maintain stopped with exit code %MAINTAIN_EXIT%.
  echo Runtime log: "%USERPROFILE%\.maintain\logs\maintain-runtime.log"
  pause
)
exit /b %MAINTAIN_EXIT%
'@
    Set-Content -LiteralPath $shortcutLauncherPath -Value $shortcutLauncher -Encoding Ascii
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
    Write-Host "Runtime: $installedVersion" -ForegroundColor Green
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
}
catch {
    Write-Host ""
    Write-Host ("INSTALL FAILED: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host "Install log: $installLogPath" -ForegroundColor Yellow
    throw
}
finally {
    if ($transcriptStarted) {
        Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
    }
}
