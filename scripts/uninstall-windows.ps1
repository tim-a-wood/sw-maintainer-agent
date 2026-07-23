$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$installRoot = Join-Path $env:LOCALAPPDATA "Programs\Maintain"
$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Maintain.lnk"
$startMenuFolder = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Maintain"
$startMenuShortcut = Join-Path $startMenuFolder "Maintain.lnk"
$taskbarShortcut = Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\Maintain.lnk"

function Try-UnpinTaskbar {
    param([string]$ShortcutPath)
    if (-not (Test-Path $ShortcutPath)) {
        return
    }
    try {
        $shell = New-Object -ComObject Shell.Application
        $folder = $shell.Namespace((Split-Path -Parent $ShortcutPath))
        $item = $folder.ParseName((Split-Path -Leaf $ShortcutPath))
        if ($null -ne $item) {
            $item.InvokeVerb("taskbarunpin")
        }
    }
    catch {
        # The pinned shortcut is removed directly below if Windows hides the verb.
    }
}

function Remove-UserPath {
    param([string]$Directory)
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $separator = [System.IO.Path]::DirectorySeparatorChar
    $normalizedDirectory = $Directory.TrimEnd($separator)
    $parts = @($current -split ";" | Where-Object {
        $_ -and $_.TrimEnd($separator) -ne $normalizedDirectory
    })
    [Environment]::SetEnvironmentVariable("Path", ($parts -join ";"), "User")
}

Write-Host ""
Write-Host "{ MAINTAIN }  UNINSTALL" -ForegroundColor Green
Write-Host ""

Try-UnpinTaskbar -ShortcutPath $startMenuShortcut
Remove-Item -Force -ErrorAction SilentlyContinue $desktopShortcut
Remove-Item -Force -ErrorAction SilentlyContinue $taskbarShortcut
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $startMenuFolder
Remove-UserPath -Directory $installRoot
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $installRoot

Write-Host "The CLI and its shortcuts were removed." -ForegroundColor Green
Write-Host "Run history, browser sign-in, and settings remain in $HOME\.maintain."
