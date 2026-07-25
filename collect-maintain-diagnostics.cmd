@echo off
setlocal EnableExtensions
set "OUTPUT=%CD%\maintain-diagnostics.zip"
set "PS_SCRIPT=%TEMP%\maintain-diagnostics-%RANDOM%-%RANDOM%.ps1"

>"%PS_SCRIPT%" echo $ErrorActionPreference = 'Stop'
>>"%PS_SCRIPT%" echo Set-StrictMode -Version Latest
>>"%PS_SCRIPT%" echo $outputPath = $env:MAINTAIN_DIAGNOSTIC_OUTPUT
>>"%PS_SCRIPT%" echo $staging = Join-Path $env:TEMP ('maintain-diagnostics-' + [Guid]::NewGuid().ToString('N'))
>>"%PS_SCRIPT%" echo New-Item -ItemType Directory -Path $staging ^| Out-Null
>>"%PS_SCRIPT%" echo function Write-JsonFile { param([string]$Path, [object]$Value) $Value ^| ConvertTo-Json -Depth 20 ^| Set-Content -Path $Path -Encoding UTF8 }
>>"%PS_SCRIPT%" echo function Copy-NewestFiles {
>>"%PS_SCRIPT%" echo     param([string]$Root, [string[]]$Patterns, [int]$Maximum = 20)
>>"%PS_SCRIPT%" echo     if (-not (Test-Path -LiteralPath $Root)) { return }
>>"%PS_SCRIPT%" echo     $files = foreach ($pattern in $Patterns) { Get-ChildItem -LiteralPath $Root -Recurse -File -Filter $pattern -ErrorAction SilentlyContinue }
>>"%PS_SCRIPT%" echo     $files = @($files ^| Sort-Object LastWriteTimeUtc -Descending -Unique ^| Select-Object -First $Maximum)
>>"%PS_SCRIPT%" echo     $index = 0
>>"%PS_SCRIPT%" echo     foreach ($file in $files) {
>>"%PS_SCRIPT%" echo         $index++
>>"%PS_SCRIPT%" echo         $parentName = Split-Path -Leaf $file.DirectoryName
>>"%PS_SCRIPT%" echo         $safeParent = $parentName -replace '[^A-Za-z0-9._-]', '_'
>>"%PS_SCRIPT%" echo         $destinationName = ('{0:D2}-{1}-{2}' -f $index, $safeParent, $file.Name)
>>"%PS_SCRIPT%" echo         Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $staging $destinationName) -Force
>>"%PS_SCRIPT%" echo     }
>>"%PS_SCRIPT%" echo }
>>"%PS_SCRIPT%" echo try {
>>"%PS_SCRIPT%" echo     $summary = [ordered]@{ collected_at_utc = [DateTime]::UtcNow.ToString('o'); computer = $env:COMPUTERNAME; user_profile = $env:USERPROFILE; powershell = $PSVersionTable.PSVersion.ToString(); python = $null; maintain = $null; playwright = $null; processes = @() }
>>"%PS_SCRIPT%" echo     try { $summary.python = (^& python --version 2^>^&1 ^| Out-String).Trim() } catch {}
>>"%PS_SCRIPT%" echo     try { $summary.maintain = (^& maintain --version 2^>^&1 ^| Out-String).Trim() } catch {}
>>"%PS_SCRIPT%" echo     try { $summary.playwright = (^& python -c "import playwright; print(getattr(playwright, '__version__', 'installed'))" 2^>^&1 ^| Out-String).Trim() } catch {}
>>"%PS_SCRIPT%" echo     $summary.processes = @(Get-CimInstance Win32_Process ^| Where-Object { $_.Name -match 'python^|chrome^|msedge^|chromium' } ^| Select-Object Name, ProcessId, CreationDate, CommandLine)
>>"%PS_SCRIPT%" echo     Write-JsonFile -Path (Join-Path $staging 'system-summary.json') -Value $summary
>>"%PS_SCRIPT%" echo     $maintainRoot = Join-Path $env:USERPROFILE '.maintain'
>>"%PS_SCRIPT%" echo     Copy-NewestFiles -Root $maintainRoot -Patterns @('*-failure.json','*-transport.json','run.json','audit.jsonl') -Maximum 20
>>"%PS_SCRIPT%" echo     $localApp = Join-Path $env:LOCALAPPDATA 'Programs\Maintain'
>>"%PS_SCRIPT%" echo     if (Test-Path -LiteralPath $localApp) { Get-ChildItem -LiteralPath $localApp -Recurse -File -ErrorAction SilentlyContinue ^| Where-Object { $_.Name -in @('browser.py','PKG-INFO') } ^| Select-Object FullName, Length, LastWriteTimeUtc ^| Export-Csv -Path (Join-Path $staging 'installed-files.csv') -NoTypeInformation }
>>"%PS_SCRIPT%" echo     if (Test-Path -LiteralPath $outputPath) { Remove-Item -LiteralPath $outputPath -Force }
>>"%PS_SCRIPT%" echo     Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $outputPath -CompressionLevel Optimal
>>"%PS_SCRIPT%" echo     Write-Host ('Created: ' + $outputPath) -ForegroundColor Green
>>"%PS_SCRIPT%" echo }
>>"%PS_SCRIPT%" echo finally { Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue }

set "MAINTAIN_DIAGNOSTIC_OUTPUT=%OUTPUT%"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%"
set "EXIT_CODE=%ERRORLEVEL%"
del /q "%PS_SCRIPT%" >nul 2>nul

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Diagnostic collection failed with exit code %EXIT_CODE%.
    pause
    exit /b %EXIT_CODE%
)

echo.
echo Upload this file to the conversation:
echo %OUTPUT%
pause
exit /b 0
