$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Version = (Get-Content -LiteralPath (Join-Path $ProjectRoot "VERSION") -Raw).Trim()
if (-not $Version) {
    throw "VERSION 文件为空，无法生成发布包名称。"
}

$DistRoot = Join-Path $ProjectRoot "dist-portable"
$BuildRoot = Join-Path $ProjectRoot "build"
$PyInstallerDist = Join-Path $ProjectRoot "dist"
$PortableName = "tg-channel-sync-$Version-windows-x64-portable"
$PortableDir = Join-Path $DistRoot $PortableName
$PortableZipPath = Join-Path $DistRoot "$PortableName.zip"
$FullName = "tg-channel-sync-$Version-windows-x64-full"
$FullDir = Join-Path $DistRoot $FullName
$FullZipPath = Join-Path $DistRoot "$FullName.zip"
$FullSourceDir = Join-Path $FullDir "app"
$BundledVenvDir = Join-Path $FullDir "venv"

if (Test-Path $BuildRoot) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
if (Test-Path $PyInstallerDist) {
    Remove-Item -LiteralPath $PyInstallerDist -Recurse -Force
}
if (Test-Path $PortableDir) {
    Remove-Item -LiteralPath $PortableDir -Recurse -Force
}
if (Test-Path $PortableZipPath) {
    Remove-Item -LiteralPath $PortableZipPath -Force
}
if (Test-Path $FullDir) {
    Remove-Item -LiteralPath $FullDir -Recurse -Force
}
if (Test-Path $FullZipPath) {
    Remove-Item -LiteralPath $FullZipPath -Force
}

New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null

$PyInstaller = Join-Path $ProjectRoot "venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $PyInstaller)) {
    $PyInstaller = "pyinstaller"
}

& $PyInstaller --clean --noconfirm "tg-channel-sync.spec"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 构建失败，退出码: $LASTEXITCODE"
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
    "sync_engine.py",
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

Compress-Archive -Path $PortableDir -DestinationPath $PortableZipPath -Force
Compress-Archive -Path $FullDir -DestinationPath $FullZipPath -Force

Remove-Item -LiteralPath $PortableDir -Recurse -Force
Remove-Item -LiteralPath $FullDir -Recurse -Force
if (Test-Path $BuildRoot) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
if (Test-Path $PyInstallerDist) {
    Remove-Item -LiteralPath $PyInstallerDist -Recurse -Force
}

Write-Host ""
Write-Host "Build completed:"
Write-Host "  Portable zip:       $PortableZipPath"
Write-Host "  Full zip:           $FullZipPath"
Write-Host "  Output folder:      $DistRoot"
