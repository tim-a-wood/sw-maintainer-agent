# Applies one Maintain update after the app closes.
#
# The app cannot replace its own private environment while it runs,
# so it starts this script detached and then exits. The script waits
# for the app process to end, downloads the requested release, runs
# that release's own installer, and starts Maintain again.
param(
    [int]$AppProcessId = 0,
    [string]$Reference = "",
    [string]$Repository = "https://github.com/tim-a-wood/sw-maintainer-agent.git",
    [switch]$NoRelaunch
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($Reference)) {
    throw "A -Reference such as refs/tags/v1.2.3 is required."
}

Write-Host ""
Write-Host "{ MAINTAIN }  UPDATE"
Write-Host ""
Write-Host "Release: $Reference"

if ($AppProcessId -gt 0) {
    Write-Host "Waiting for the app (process $AppProcessId) to close..."
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        $running = Get-Process -Id $AppProcessId -ErrorAction SilentlyContinue
        if ($null -eq $running) { break }
        Start-Sleep -Milliseconds 500
    }
    $running = Get-Process -Id $AppProcessId -ErrorAction SilentlyContinue
    if ($null -ne $running) {
        throw "The app did not close. Close Maintain and run the update again."
    }
}

$installRoot = Join-Path $env:LOCALAPPDATA "Programs\Maintain"
$updateLogPath = Join-Path $installRoot "update.log"
$installLogPath = Join-Path $installRoot "install.log"

# FR-V11: an update that fails must leave evidence. The console window
# closes with the process, so a person who looks away sees only that
# the version did not change. The transcript outlives the window.
$transcriptStarted = $false
try {
    if (-not (Test-Path -LiteralPath $installRoot)) {
        New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
    }
    Start-Transcript -LiteralPath $updateLogPath -Append | Out-Null
    $transcriptStarted = $true
    Write-Host "Update log: $updateLogPath"
}
catch {
    Write-Host "The update log could not be started: $($_.Exception.Message)"
}

function Get-InstalledVersion {
    <#
        The one honest answer to "did the update take?". Exit codes from
        a called .ps1 are ambiguous — a script that ends without exit
        leaves $LASTEXITCODE from whatever native command ran last — so
        the installed runtime is asked directly.
    #>
    $runtime = Join-Path $installRoot "venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $runtime -PathType Leaf)) { return "" }
    try {
        $reported = & $runtime -c "import maintain; print(maintain.__version__)" 2>$null
        if ($LASTEXITCODE -ne 0) { return "" }
        return ([string]$reported).Trim()
    }
    catch {
        return ""
    }
}

$wanted = ($Reference -replace "^refs/tags/", "") -replace "^v", ""
$before = Get-InstalledVersion
Write-Host "Installed now: $(if ($before) { $before } else { 'not found' })"
Write-Host "Wanted: $wanted"

$work = Join-Path ([IO.Path]::GetTempPath()) (
    "maintain-update-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $work | Out-Null

try {
    Write-Host "Downloading the release..."
    & git -C $work init --quiet
    if ($LASTEXITCODE -ne 0) { throw "git init failed." }
    & git -C $work fetch --quiet --depth 1 $Repository `
        "+${Reference}:refs/maintain/update"
    if ($LASTEXITCODE -ne 0) { throw "The release could not be downloaded." }
    & git -C $work checkout --quiet --detach refs/maintain/update
    if ($LASTEXITCODE -ne 0) { throw "The release could not be checked out." }

    $installer = Join-Path $work "scripts\install-windows.ps1"
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw "The release does not contain scripts\install-windows.ps1."
    }

    # The release's installer resolves, verifies, and installs the same
    # reference itself. A pinned MAINTAIN_PACKAGE_SOURCE is CI's tool
    # for offline installs; outside CI a leftover pin would make every
    # update silently reinstall the pinned old build, so the update
    # clears it and names its own release.
    if ([string]::IsNullOrEmpty($env:CI)) {
        $env:MAINTAIN_PACKAGE_SOURCE = $null
        $env:MAINTAIN_PACKAGE_REF = $Reference
    }
    # FR-V11: the outcome is checked, not the exit status. "$?" is true
    # whenever the installer's own last statement succeeded, so a failed
    # install could report success and start the old version again —
    # which is exactly what "the last update did not take" looked like.
    & $installer
    $after = Get-InstalledVersion
    if (-not $after) {
        throw ("The installer ran but no Maintain runtime answers. " +
               "See $installLogPath")
    }
    if ($wanted -and $after -ne $wanted) {
        throw ("The installer ran but the version is still $after, not " +
               "$wanted. See $installLogPath")
    }
    Write-Host "Installed: $after"

    if (-not $NoRelaunch) {
        $launcher = Join-Path $env:LOCALAPPDATA "Programs\Maintain\Maintain.cmd"
        if (Test-Path -LiteralPath $launcher -PathType Leaf) {
            Write-Host "Starting Maintain..."
            Start-Process -FilePath $launcher | Out-Null
        }
    }
    Write-Host "The update is complete."
}
catch {
    Write-Host ""
    Write-Warning $_
    Write-Host "The update failed."
    Write-Host "Update log: $updateLogPath"
    Write-Host "Install log: $installLogPath"
    Write-Host "Send both files to report this."
    if ([string]::IsNullOrEmpty($env:CI)) {
        Read-Host "Press Enter to close this window"
    }
    if ($transcriptStarted) {
        Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
    }
    exit 1
}
finally {
    if (Test-Path -LiteralPath $work) {
        Remove-Item -LiteralPath $work -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
    if ($transcriptStarted) {
        Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
    }
}
