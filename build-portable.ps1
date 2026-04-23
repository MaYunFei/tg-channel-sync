$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.IO.Compression.FileSystem

function Compress-DirectoryWithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceDir,
        [Parameter(Mandatory = $true)]
        [string]$DestinationZip,
        [int]$MaxAttempts = 5,
        [int]$DelaySeconds = 2
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            if (Test-Path $DestinationZip) {
                Remove-Item -LiteralPath $DestinationZip -Force
            }
            [System.IO.Compression.ZipFile]::CreateFromDirectory($SourceDir, $DestinationZip)
            return
        } catch {
            if ($attempt -ge $MaxAttempts) {
                throw
            }
            Write-Host "Archive retry $attempt/$MaxAttempts failed for $DestinationZip. Waiting $DelaySeconds seconds..."
            Start-Sleep -Seconds $DelaySeconds
        }
    }
}

function Remove-PathWithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [switch]$Recurse,
        [int]$MaxAttempts = 5,
        [int]$DelaySeconds = 2
    )

    if (-not (Test-Path $Path)) {
        return
    }

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            if ($Recurse) {
                Remove-Item -LiteralPath $Path -Recurse -Force
            } else {
                Remove-Item -LiteralPath $Path -Force
            }
            return
        } catch {
            if ($attempt -ge $MaxAttempts) {
                throw
            }
            Write-Host "Delete retry $attempt/$MaxAttempts failed for $Path. Waiting $DelaySeconds seconds..."
            Start-Sleep -Seconds $DelaySeconds
        }
    }
}

function Remove-PathBestEffort {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [switch]$Recurse
    )

    try {
        if ($Recurse) {
            Remove-PathWithRetry -Path $Path -Recurse
        } else {
            Remove-PathWithRetry -Path $Path
        }
    } catch {
        Write-Warning "Cleanup skipped for ${Path}: $($_.Exception.Message)"
    }
}

function Cleanup-StaleStageDirs {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TempRoot,
        [int]$OlderThanHours = 12
    )

    if (-not (Test-Path $TempRoot)) {
        return
    }

    $cutoff = (Get-Date).AddHours(-1 * $OlderThanHours)
    Get-ChildItem -LiteralPath $TempRoot -Directory -Filter "package-*" -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.LastWriteTime -lt $cutoff) {
            Remove-PathBestEffort -Path $_.FullName -Recurse
        }
    }
}

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Version = (Get-Content -LiteralPath (Join-Path $ProjectRoot "VERSION") -Raw).Trim()
if (-not $Version) {
    throw "VERSION file is empty. Cannot build release package name."
}

$DistRoot = Join-Path $ProjectRoot "dist-portable"
$BuildRoot = Join-Path $ProjectRoot "build"
$PyInstallerDist = Join-Path $ProjectRoot "dist"
$TempRoot = Join-Path $ProjectRoot "temp"
$StageRoot = Join-Path $TempRoot ("package-" + [Guid]::NewGuid().ToString("N"))
$PortableName = "tg-channel-sync-$Version-windows-x64-portable"
$PortableDir = Join-Path $StageRoot $PortableName
$PortableZipPath = Join-Path $DistRoot "$PortableName.zip"
$FullName = "tg-channel-sync-$Version-windows-x64-full"
$FullDir = Join-Path $StageRoot $FullName
$FullZipPath = Join-Path $DistRoot "$FullName.zip"
$FullSourceDir = Join-Path $FullDir "app"
$BundledVenvDir = Join-Path $FullDir "venv"

if (Test-Path $BuildRoot) {
    Remove-PathBestEffort -Path $BuildRoot -Recurse
}
if (Test-Path $PyInstallerDist) {
    Remove-PathBestEffort -Path $PyInstallerDist -Recurse
}
if (Test-Path $PortableZipPath) {
    Remove-PathWithRetry -Path $PortableZipPath
}
if (Test-Path $FullZipPath) {
    Remove-PathWithRetry -Path $FullZipPath
}

New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null
New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
Cleanup-StaleStageDirs -TempRoot $TempRoot

$PyInstaller = Join-Path $ProjectRoot "venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $PyInstaller)) {
    $PyInstaller = "pyinstaller"
}

& $PyInstaller --clean --noconfirm "tg-channel-sync.spec"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code: $LASTEXITCODE"
}

New-Item -ItemType Directory -Path $PortableDir -Force | Out-Null
Copy-Item -Path (Join-Path $PyInstallerDist "tg-channel-sync\*") -Destination $PortableDir -Recurse -Force

New-Item -ItemType Directory -Path $FullSourceDir -Force | Out-Null
New-Item -ItemType Directory -Path $BundledVenvDir -Force | Out-Null

$AppFiles = @(
    ".gitignore",
    "README.md",
    "LICENSE",
    "requirements.txt",
    "VERSION",
    "app_config.py",
    "app_paths.py",
    "bot_engine.py",
    "database.py",
    "main.py",
    "server_runtime.py",
    "services",
    "sync_worker",
    "static"
)

foreach ($item in $AppFiles) {
    $sourcePath = Join-Path $ProjectRoot $item
    $destinationPath = Join-Path $FullSourceDir $item
    if (Test-Path $sourcePath -PathType Container) {
        Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Recurse -Force
    } else {
        $destinationDir = Split-Path -Parent $destinationPath
        if (-not (Test-Path $destinationDir)) {
            New-Item -ItemType Directory -Path $destinationDir -Force | Out-Null
        }
        Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force
    }
}

Copy-Item -Path (Join-Path $ProjectRoot "venv\*") -Destination $BundledVenvDir -Recurse -Force

$StartBat = @"
@echo off
setlocal
cd /d "%~dp0app"
..\venv\Scripts\python.exe main.py
"@
Set-Content -LiteralPath (Join-Path $FullDir "start.bat") -Value $StartBat -Encoding ASCII

$StartNoBrowserBat = @"
@echo off
setlocal
cd /d "%~dp0app"
set TG_CHANNEL_SYNC_NO_BROWSER=1
..\venv\Scripts\python.exe main.py
"@
Set-Content -LiteralPath (Join-Path $FullDir "start-no-browser.bat") -Value $StartNoBrowserBat -Encoding ASCII

Compress-DirectoryWithRetry -SourceDir $PortableDir -DestinationZip $PortableZipPath
Compress-DirectoryWithRetry -SourceDir $FullDir -DestinationZip $FullZipPath

if (Test-Path $PyInstallerDist) {
    Remove-PathBestEffort -Path $PyInstallerDist -Recurse
}

Write-Host ""
Write-Host "Build completed:"
Write-Host "  Portable zip:       $PortableZipPath"
Write-Host "  Full zip:           $FullZipPath"
Write-Host "  Output folder:      $DistRoot"
Write-Host "  Temp stage:         $StageRoot"
