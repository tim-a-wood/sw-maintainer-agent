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
$repositoryUrl = "https://github.com/tim-a-wood/sw-maintainer-agent.git"
# An installer installs the newest release, never a branch tip. A
# branch default installed whatever that branch held, which is how a
# fresh install landed on an old version while releases moved on.
# The newest v1.2.3 tag is resolved below, once Git is found.
$repositoryRef = ""
# The self-updater pins one release: MAINTAIN_PACKAGE_REF names the
# tag (refs/tags/v1.2.3) that this install must resolve and verify.
$repositoryRefOverride = [Environment]::GetEnvironmentVariable("MAINTAIN_PACKAGE_REF")
if (-not [string]::IsNullOrWhiteSpace($repositoryRefOverride)) {
    $repositoryRef = $repositoryRefOverride.Trim()
}
$packageSource = $null
$packageSourceOverridden = $false
$packageSourceOverride = [Environment]::GetEnvironmentVariable("MAINTAIN_PACKAGE_SOURCE")
$resolvedCommit = ""
$expectedVersion = ""
$sourceRoot = ""

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

function Resolve-LatestReleaseTag {
    param(
        [string]$GitPath,
        [string]$Repository
    )
    # The newest v1.2.3 tag in the repository. Annotated tags appear
    # twice, once peeled with ^{}; both name the same release.
    $result = (& $GitPath ls-remote --tags $Repository | Out-String).Trim()
    Assert-NativeCommand -Action "Looking for the newest Maintain release"
    $best = $null
    $bestName = ""
    foreach ($line in ($result -split "`n")) {
        $match = [regex]::Match(
            $line.Trim(),
            "^[0-9a-fA-F]{40}\s+refs/tags/(?<tag>v\d+(\.\d+)*)(\^\{\})?$"
        )
        if (-not $match.Success) { continue }
        $name = $match.Groups["tag"].Value
        $parsed = $null
        if (-not [Version]::TryParse($name.Substring(1), [ref]$parsed)) { continue }
        if ($null -eq $best -or $parsed -gt $best) {
            $best = $parsed
            $bestName = $name
        }
    }
    if (-not $bestName) {
        throw "This repository has no published Maintain release to install."
    }
    return "refs/tags/$bestName"
}

function Resolve-RemoteCommit {
    param(
        [string]$GitPath,
        [string]$Repository,
        [string]$Reference
    )
    # An annotated tag resolves to a tag object; its peeled ^{} line
    # names the commit. A branch or a lightweight tag has one line.
    $result = (& $GitPath ls-remote $Repository $Reference "$Reference^{}" |
        Out-String).Trim()
    Assert-NativeCommand -Action "Resolving the requested Maintain source"
    $commit = ""
    foreach ($line in ($result -split "`n")) {
        $match = [regex]::Match(
            $line.Trim(),
            "^(?<commit>[0-9a-fA-F]{40})\s+(?<ref>\S+)$"
        )
        if (-not $match.Success) { continue }
        if ($match.Groups["ref"].Value -eq "$Reference^{}") {
            $commit = $match.Groups["commit"].Value
        }
        elseif ($match.Groups["ref"].Value -eq $Reference -and -not $commit) {
            $commit = $match.Groups["commit"].Value
        }
    }
    if (-not $commit) {
        throw "GitHub did not return one exact commit for $Reference."
    }
    return $commit.ToLowerInvariant()
}

function New-SourceCheckout {
    param(
        [string]$GitPath,
        [string]$Repository,
        [string]$Commit
    )
    $root = Join-Path ([IO.Path]::GetTempPath()) (
        "maintain-install-" + [Guid]::NewGuid().ToString("N")
    )
    New-Item -ItemType Directory -Path $root | Out-Null
    & $GitPath -C $root init --quiet
    Assert-NativeCommand -Action "Preparing the Maintain source checkout"
    & $GitPath -C $root remote add origin $Repository
    Assert-NativeCommand -Action "Configuring the Maintain source checkout"
    & $GitPath -C $root fetch --quiet --depth 1 origin $Commit
    Assert-NativeCommand -Action "Downloading the resolved Maintain commit"
    & $GitPath -C $root checkout --quiet --detach FETCH_HEAD
    Assert-NativeCommand -Action "Checking out the resolved Maintain commit"
    $actual = (& $GitPath -C $root rev-parse HEAD | Out-String).Trim().ToLowerInvariant()
    Assert-NativeCommand -Action "Verifying the Maintain source checkout"
    if ($actual -ne $Commit) {
        throw "The downloaded Maintain source did not match commit $Commit."
    }
    return $root
}

function Get-ProjectVersion {
    param([string]$SourceRoot)
    $projectFile = Join-Path $SourceRoot "pyproject.toml"
    if (-not (Test-Path -LiteralPath $projectFile -PathType Leaf)) {
        throw "The resolved Maintain source is missing pyproject.toml."
    }
    $text = Get-Content -LiteralPath $projectFile -Raw
    $match = [regex]::Match($text, '(?m)^version\s*=\s*"(?<version>[^"]+)"\s*$')
    if (-not $match.Success) {
        throw "The resolved Maintain source does not declare one project version."
    }
    return $match.Groups["version"].Value
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
    if (-not $packageSourceOverridden) {
        if ([string]::IsNullOrWhiteSpace($repositoryRef)) {
            $repositoryRef = Resolve-LatestReleaseTag `
                -GitPath $gitCommand -Repository $repositoryUrl
        }
        Write-Host "Release: $repositoryRef"
        $resolvedCommit = Resolve-RemoteCommit `
            -GitPath $gitCommand -Repository $repositoryUrl -Reference $repositoryRef
        $sourceRoot = New-SourceCheckout `
            -GitPath $gitCommand -Repository $repositoryUrl -Commit $resolvedCommit
        $expectedVersion = Get-ProjectVersion -SourceRoot $sourceRoot
        $packageSource = "${sourceRoot}[browser]"
        Write-Host "Source commit: $resolvedCommit"
        Write-Host "Expected version: Maintain $expectedVersion"
    }

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
        throw (
            "Installing resolved commit $resolvedCommit failed with exit code $LASTEXITCODE. " +
            "Maintain was not reported as updated; review the install log and try again."
        )
    }

    $installedVersion = (& $venvPython -m maintain --version | Out-String).Trim()
    Assert-NativeCommand -Action "Checking the installed Maintain runtime"
    if (-not $installedVersion) {
        throw "The installed Maintain runtime did not report its version."
    }
    if (-not $packageSourceOverridden -and
            $installedVersion -ne "Maintain $expectedVersion") {
        throw (
            "The private runtime reported '$installedVersion' after installing commit " +
            "$resolvedCommit; expected 'Maintain $expectedVersion'."
        )
    }

    Write-Host "Preparing the browser used by Copilot and ChatGPT..."
    & $venvPython -m playwright install chromium
    Assert-NativeCommand -Action "Installing Chromium"

    Write-Host "Installing the Maintain desktop UI..."
    & $venvPython -m pip install --disable-pip-version-check --no-cache-dir `
        "PySide6-Essentials>=6.6,<7"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "The desktop UI could not be installed; the CLI still works." `
            -ForegroundColor Yellow
    }

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

    $uiExecutable = Join-Path (Split-Path -Parent $venvPython) "maintain-ui.exe"
    if (Test-Path -LiteralPath $uiExecutable) {
        foreach ($uiShortcutPath in @(
                (Join-Path $desktop "Maintain UI.lnk"),
                (Join-Path $startMenuFolder "Maintain UI.lnk"))) {
            $uiShell = New-Object -ComObject WScript.Shell
            $uiShortcut = $uiShell.CreateShortcut($uiShortcutPath)
            $uiShortcut.TargetPath = $uiExecutable
            $uiShortcut.WorkingDirectory = $env:USERPROFILE
            $uiShortcut.IconLocation = "$iconPath,0"
            $uiShortcut.Description = "Maintain Simple UI - the guided packet exchange"
            $uiShortcut.Save()
        }
    }

    $pinned = Try-PinTaskbar -ShortcutPath $startMenuShortcut

    Write-Host ""
    Write-Host "Installed: $installRoot" -ForegroundColor Green
    Write-Host "Runtime: $installedVersion" -ForegroundColor Green
    if ($resolvedCommit) {
        Write-Host "Source commit: $resolvedCommit" -ForegroundColor Green
    }
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
    if ($sourceRoot -and (Test-Path -LiteralPath $sourceRoot)) {
        $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd(
            [IO.Path]::DirectorySeparatorChar
        )
        $resolvedSource = [IO.Path]::GetFullPath($sourceRoot)
        if ($resolvedSource.StartsWith(
                $tempRoot + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase) -and
                (Split-Path -Leaf $resolvedSource).StartsWith("maintain-install-")) {
            Remove-Item -LiteralPath $resolvedSource -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    if ($transcriptStarted) {
        Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
    }
}
