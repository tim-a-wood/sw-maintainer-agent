$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Installs or updates Maintain from this clone into a private environment
# under %LOCALAPPDATA%, puts `maintain` on the user PATH, and creates or
# refreshes the desktop and Start Menu shortcuts. Safe to run repeatedly:
# re-running is how you update.

$installRoot = Join-Path $env:LOCALAPPDATA "Programs\Maintain"
$venvRoot = Join-Path $installRoot "venv"
$launcherPath = Join-Path $installRoot "Maintain.cmd"
$shortcutLauncherPath = Join-Path $installRoot "Maintain-shortcut.cmd"
$runtimeLauncherPath = Join-Path $installRoot "launch-maintain.ps1"
$installLogPath = Join-Path $installRoot "install.log"
$iconPath = Join-Path $installRoot "maintain.ico"
$repoRoot = Split-Path -Parent $PSScriptRoot
$iconSource = Join-Path $repoRoot "assets\maintain.ico.b64"
$expectedVersion = ""

function Assert-NativeCommand {
    param([string]$Action)
    if ($LASTEXITCODE -ne 0) {
        throw "$Action failed with exit code $LASTEXITCODE."
    }
}

function Test-PythonCandidate {
    param([string]$Command, [string[]]$Prefix)
    try {
        & $Command @Prefix -c "import sys, venv; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" `
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
    throw "Python 3.9 or later is required. Install it from https://www.python.org/downloads/windows/ and run this installer again."
}

function Find-Git {
    $git = Get-Command "git.exe" -ErrorAction SilentlyContinue
    if ($null -eq $git) {
        throw "Git is required. Install Git for Windows from https://git-scm.com/download/win and run this installer again."
    }
    return $git.Source
}

function Update-SourceClone {
    param([string]$GitPath, [string]$Root)
    if (-not (Test-Path -LiteralPath (Join-Path $Root ".git"))) {
        Write-Host "Source: $Root (not a Git clone; installing it as-is)"
        return
    }
    $status = (& $GitPath -C $Root status --porcelain | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Source: $Root (could not read Git status; installing it as-is)" -ForegroundColor Yellow
        return
    }
    if ($status) {
        Write-Host "Source: $Root (uncommitted changes present; installing them without pulling)" -ForegroundColor Yellow
        return
    }
    Write-Host "Updating the source clone..."
    & $GitPath -C $Root pull --ff-only --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Could not fast-forward this clone; installing the current checkout." -ForegroundColor Yellow
        return
    }
    $commit = (& $GitPath -C $Root rev-parse --short HEAD | Out-String).Trim()
    $branch = (& $GitPath -C $Root rev-parse --abbrev-ref HEAD | Out-String).Trim()
    Write-Host "Source: $Root ($branch at $commit)"
}

function Get-ProjectVersion {
    param([string]$SourceRoot)
    $projectFile = Join-Path $SourceRoot "pyproject.toml"
    if (-not (Test-Path -LiteralPath $projectFile -PathType Leaf)) {
        throw "This does not look like the Maintain source: $projectFile is missing."
    }
    $text = Get-Content -LiteralPath $projectFile -Raw
    $match = [regex]::Match($text, '(?m)^version\s*=\s*"(?<version>[^"]+)"\s*$')
    if (-not $match.Success) {
        throw "The Maintain source does not declare one project version."
    }
    return $match.Groups["version"].Value
}

function Test-PrivateEnvironment {
    param([string]$PythonPath)
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return $false
    }
    try {
        & $PythonPath -c "import pip, sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" `
            2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Test-LegacyRuntime {
    # The 0.9 runtime installs the distribution "sw-maintainer-agent", which
    # ships an import package also called "maintain". Installing this version
    # on top of it would leave every 0.9 module in place and the wrong CLI
    # could keep running, so the environment is rebuilt instead.
    param([string]$PythonPath)
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return $false
    }
    try {
        & $PythonPath -m pip show sw-maintainer-agent 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Add-UserPath {
    # Prepend, and move an existing entry to the front. A pip install of the
    # 0.9 package leaves a maintain.exe in Python's Scripts directory, which
    # is also on the user PATH; appending would let that copy win.
    param([string]$Directory)
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $separator = [IO.Path]::DirectorySeparatorChar
    $normalized = $Directory.TrimEnd($separator)
    $parts = @($current -split ";" | Where-Object {
        $_ -and $_.Trim().TrimEnd($separator) -ne $normalized
    })
    $updated = (@($Directory) + $parts) -join ";"
    if ($updated -ne $current) {
        [Environment]::SetEnvironmentVariable("Path", $updated, "User")
    }
}

function New-MaintainShortcut {
    param([string]$Path)
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $shortcutLauncherPath
    $shortcut.Arguments = ""
    $shortcut.WorkingDirectory = $env:USERPROFILE
    $shortcut.IconLocation = "$iconPath,0"
    $shortcut.Description = "Maintain: a chatbot-assisted software maintenance workflow"
    $shortcut.Save()
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

    $python = Find-Python
    $pythonCommand = $python.Command
    $pythonPrefix = $python.Prefix
    $gitCommand = Find-Git
    Write-Host "Python: $pythonCommand $($pythonPrefix -join ' ')"
    Write-Host "Git: $gitCommand"

    Update-SourceClone -GitPath $gitCommand -Root $repoRoot
    $expectedVersion = Get-ProjectVersion -SourceRoot $repoRoot
    Write-Host "Expected version: maintain $expectedVersion"

    $venvPython = Join-Path $venvRoot "Scripts\python.exe"
    $rebuildReason = ""
    if (Test-Path -LiteralPath $venvRoot) {
        if (-not (Test-PrivateEnvironment -PythonPath $venvPython)) {
            $rebuildReason = "The existing private environment is unusable."
        }
        elseif (Test-LegacyRuntime -PythonPath $venvPython) {
            $rebuildReason = "Found the 0.9 runtime, which shares the 'maintain' package name."
        }
    }
    if ($rebuildReason) {
        if ((Split-Path -Parent $venvRoot) -ne $installRoot) {
            throw "Refusing to replace an unexpected Python environment path: $venvRoot"
        }
        Write-Host "$rebuildReason Rebuilding it..." -ForegroundColor Yellow
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

    Write-Host "Installing Maintain..."
    & $venvPython -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        Write-Host "pip could not update; continuing with the current pip." -ForegroundColor Yellow
    }
    & $venvPython -m pip install --disable-pip-version-check --upgrade --force-reinstall $repoRoot
    Assert-NativeCommand -Action "Installing Maintain"

    $installedVersion = (& $venvPython -m maintain --version | Out-String).Trim()
    Assert-NativeCommand -Action "Checking the installed Maintain runtime"
    if ($installedVersion -ne "maintain $expectedVersion") {
        throw "The installed runtime reported '$installedVersion'; expected 'maintain $expectedVersion'."
    }

    if (-not (Test-Path -LiteralPath $iconSource)) {
        throw "The Maintain icon is missing from this clone: $iconSource"
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
$installRoot = Split-Path -Parent $PSCommandPath
$venvPython = Join-Path $installRoot "venv\Scripts\python.exe"

try {
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

exit $exitCode
'@
    Set-Content -LiteralPath $runtimeLauncherPath -Value $runtimeLauncher -Encoding UTF8

    $launcher = @'
@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-maintain.ps1" %*
set "MAINTAIN_EXIT=%ERRORLEVEL%"
exit /b %MAINTAIN_EXIT%
'@
    Set-Content -LiteralPath $launcherPath -Value $launcher -Encoding Ascii

    # The desktop shortcut opens a session where `maintain` is on PATH. Maintain
    # works inside the repository you are maintaining, so it starts a prompt
    # rather than assuming a project.
    $shortcutLauncher = @'
@echo off
setlocal EnableExtensions
title Maintain
set "PATH=%~dp0;%PATH%"
echo.
echo   { MAINTAIN }
echo.
echo   Change to the project you want to maintain, then run: maintain
echo   For example:  cd %%USERPROFILE%%\source\my-project
echo.
cd /d "%USERPROFILE%"
cmd /k
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

    $repomix = Get-Command "repomix.cmd", "repomix" -ErrorAction SilentlyContinue |
        Select-Object -First 1

    # Which `maintain` will a NEW terminal run? The current process PATH is
    # stale after this script edits the user PATH, so rebuild the effective
    # search order from the stored machine and user values and walk it.
    $effectivePath = @(
        [Environment]::GetEnvironmentVariable("Path", "Machine"),
        [Environment]::GetEnvironmentVariable("Path", "User")
    ) -join ";"
    $winner = $null
    foreach ($directory in ($effectivePath -split ";" | Where-Object { $_ })) {
        foreach ($leaf in @("maintain.cmd", "maintain.exe", "maintain.bat")) {
            $candidate = Join-Path $directory.Trim() $leaf
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                $winner = $candidate
                break
            }
        }
        if ($winner) { break }
    }

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
    if ($null -eq $repomix) {
        Write-Host ""
        Write-Host "Repomix was not found. Maintain needs it to build handoff packages." -ForegroundColor Yellow
        Write-Host "Install Node.js from https://nodejs.org/ then run: npm install -g repomix" -ForegroundColor Yellow
    }
    else {
        Write-Host "Repomix: $($repomix.Source)" -ForegroundColor Green
    }
    Write-Host ""
    if ($null -eq $winner) {
        Write-Host "A new terminal will find: nothing yet — sign out and back in if PATH does not refresh." -ForegroundColor Yellow
    }
    elseif ((Split-Path -Parent $winner) -eq $installRoot) {
        Write-Host "A new terminal will run: $winner" -ForegroundColor Green
    }
    else {
        Write-Host "NOTE: another 'maintain' is also on your PATH:" -ForegroundColor Yellow
        Write-Host "  $winner" -ForegroundColor Yellow
        Write-Host "This installer moved its own folder to the front of your user PATH," -ForegroundColor Yellow
        Write-Host "so a new terminal should now run: $launcherPath" -ForegroundColor Yellow
        Write-Host "If the old one still wins, remove it with:" -ForegroundColor Yellow
        Write-Host "    py -3 -m pip uninstall -y sw-maintainer-agent maintain" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "Confirm with: maintain --version   (expects: maintain $expectedVersion)"
    Write-Host "Run this installer again whenever you want to update."
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
