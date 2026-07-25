[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$outputPath = [IO.Path]::GetFullPath($OutputPath)
$staging = Join-Path $env:TEMP ("maintain-diagnostics-" + [Guid]::NewGuid().ToString("N"))
$installRoot = Join-Path $env:LOCALAPPDATA "Programs\Maintain"
$venvPython = Join-Path $installRoot "venv\Scripts\python.exe"

function Write-JsonFile {
    param(
        [string]$Path,
        [object]$Value
    )
    $Value | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Invoke-CapturedCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments = @()
    )
    $resolved = $FilePath
    if (-not [IO.Path]::IsPathRooted($FilePath)) {
        $command = Get-Command $FilePath -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            return [ordered]@{
                available = $false
                path = $null
                exit_code = $null
                output = $null
                error = "Command was not found."
            }
        }
        $resolved = $command.Source
    }
    elseif (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        return [ordered]@{
            available = $false
            path = $FilePath
            exit_code = $null
            output = $null
            error = "File does not exist."
        }
    }
    try {
        $output = (& $resolved @Arguments 2>&1 | Out-String).Trim()
        return [ordered]@{
            available = $true
            path = $resolved
            exit_code = $LASTEXITCODE
            output = $output
            error = $null
        }
    }
    catch {
        return [ordered]@{
            available = $true
            path = $resolved
            exit_code = $null
            output = $null
            error = $_.Exception.Message
        }
    }
}

function Get-CommandResolution {
    param([string[]]$Names)
    $values = @()
    foreach ($name in $Names) {
        $commands = @(Get-Command $name -All -ErrorAction SilentlyContinue)
        if (-not $commands) {
            $values += [ordered]@{
                requested = $name
                found = $false
                command_type = $null
                path = $null
                version = $null
            }
            continue
        }
        foreach ($command in $commands) {
            $path = $command.Source
            if ($command.Path) {
                $path = $command.Path
            }
            $values += [ordered]@{
                requested = $name
                found = $true
                command_type = $command.CommandType.ToString()
                path = $path
                version = $command.Version.ToString()
            }
        }
    }
    return $values
}

function Copy-NewestSafeBrowserEvidence {
    param(
        [string]$Root,
        [int]$Maximum = 20
    )
    if (-not (Test-Path -LiteralPath $Root)) {
        return
    }
    $separator = [IO.Path]::DirectorySeparatorChar
    $browserMarker = $separator + "artifacts" + $separator + "browser" + $separator
    $files = @(
        Get-ChildItem -LiteralPath $Root -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object {
                ($_.Name -like "*-failure.json" -or $_.Name -like "*-transport.json") -and
                $_.FullName.IndexOf(
                    $browserMarker, [StringComparison]::OrdinalIgnoreCase
                ) -ge 0
            } |
            Sort-Object LastWriteTimeUtc -Descending -Unique |
            Select-Object -First $Maximum
    )
    $index = 0
    foreach ($file in $files) {
        $index++
        $parentName = Split-Path -Leaf $file.DirectoryName
        $safeParent = $parentName -replace "[^A-Za-z0-9._-]", "_"
        $destinationName = "{0:D2}-{1}-{2}" -f $index, $safeParent, $file.Name
        Copy-Item -LiteralPath $file.FullName `
            -Destination (Join-Path $staging $destinationName) -Force
    }
}

New-Item -ItemType Directory -Path $staging | Out-Null
try {
    $collectionErrors = @()
    $os = $null
    $processes = @()
    try {
        $os = Get-CimInstance Win32_OperatingSystem |
            Select-Object Caption, Version, BuildNumber, OSArchitecture
    }
    catch {
        $collectionErrors += "Operating-system metadata: $($_.Exception.Message)"
    }
    try {
        $processes = @(
            Get-CimInstance Win32_Process |
                Where-Object { $_.Name -match "python|chrome|msedge|chromium" } |
                Select-Object Name, ProcessId, CreationDate
        )
    }
    catch {
        $collectionErrors += "Process metadata: $($_.Exception.Message)"
    }
    $installLog = Join-Path $installRoot "install.log"
    $installLogItem = Get-Item -LiteralPath $installLog -ErrorAction SilentlyContinue
    $runtimeLog = Join-Path $env:USERPROFILE ".maintain\logs\maintain-runtime.log"
    $runtimeLogItem = Get-Item -LiteralPath $runtimeLog -ErrorAction SilentlyContinue
    $summary = [ordered]@{
        collected_at_utc = [DateTime]::UtcNow.ToString("o")
        computer = $env:COMPUTERNAME
        user_profile = $env:USERPROFILE
        powershell = $PSVersionTable.PSVersion.ToString()
        operating_system = $os
        install = [ordered]@{
            root = $installRoot
            root_exists = Test-Path -LiteralPath $installRoot
            private_python = $venvPython
            private_python_exists = Test-Path -LiteralPath $venvPython -PathType Leaf
            install_log = $installLog
            install_log_exists = $null -ne $installLogItem
            install_log_updated_utc = if ($null -ne $installLogItem) {
                $installLogItem.LastWriteTimeUtc.ToString("o")
            }
            else {
                $null
            }
            runtime_log = $runtimeLog
            runtime_log_exists = $null -ne $runtimeLogItem
            runtime_log_updated_utc = if ($null -ne $runtimeLogItem) {
                $runtimeLogItem.LastWriteTimeUtc.ToString("o")
            }
            else {
                $null
            }
        }
        commands = [ordered]@{
            private_python = Invoke-CapturedCommand -FilePath $venvPython -Arguments @("--version")
            maintain = Invoke-CapturedCommand -FilePath $venvPython `
                -Arguments @("-m", "maintain", "--version")
            pip = Invoke-CapturedCommand -FilePath $venvPython `
                -Arguments @("-m", "pip", "--version")
            playwright = Invoke-CapturedCommand -FilePath $venvPython `
                -Arguments @("-m", "playwright", "--version")
            git = Invoke-CapturedCommand -FilePath "git.exe" -Arguments @("--version")
        }
        command_resolution = Get-CommandResolution -Names @(
            "maintain", "python", "py", "git", "powershell"
        )
        processes = $processes
        collection_errors = $collectionErrors
    }
    Write-JsonFile -Path (Join-Path $staging "system-summary.json") -Value $summary

    if (Test-Path -LiteralPath $installRoot) {
        Get-ChildItem -LiteralPath $installRoot -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -in @(
                    "browser.py", "METADATA", "WHEEL", "Maintain.cmd",
                    "Maintain-shortcut.cmd", "launch-maintain.ps1", "install.log"
                )
            } |
            Select-Object FullName, Length, LastWriteTimeUtc |
            Export-Csv -LiteralPath (Join-Path $staging "installed-files.csv") `
                -NoTypeInformation
    }
    if (Test-Path -LiteralPath $installLog -PathType Leaf) {
        Copy-Item -LiteralPath $installLog -Destination (Join-Path $staging "install.log") -Force
    }

    $runsRoot = Join-Path $env:USERPROFILE ".maintain\runs"
    Copy-NewestSafeBrowserEvidence -Root $runsRoot -Maximum 20

    if (Test-Path -LiteralPath $outputPath) {
        Remove-Item -LiteralPath $outputPath -Force
    }
    Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $outputPath `
        -CompressionLevel Optimal
    Write-Host ("Created: " + $outputPath) -ForegroundColor Green
}
finally {
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
}
