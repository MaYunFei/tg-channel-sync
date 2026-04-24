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

function Copy-DirectoryContentsFiltered {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceDir,
        [Parameter(Mandatory = $true)]
        [string]$DestinationDir,
        [string[]]$ExcludeDirNames = @(),
        [string[]]$ExcludeFilePatterns = @(),
        [switch]$SkipPyCache
    )

    if (-not (Test-Path $SourceDir)) {
        throw "Source directory not found: $SourceDir"
    }

    New-Item -ItemType Directory -Path $DestinationDir -Force | Out-Null

    $sourceRoot = (Resolve-Path -LiteralPath $SourceDir).Path
    $allDirs = Get-ChildItem -LiteralPath $SourceDir -Recurse -Directory -Force
    foreach ($dir in $allDirs) {
        $relativeDir = $dir.FullName.Substring($sourceRoot.Length).TrimStart('\')
        if (-not $relativeDir) {
            continue
        }
        $segments = $relativeDir -split '[\\/]'
        if ($SkipPyCache -and ($segments -contains "__pycache__")) {
            continue
        }
        if (($segments | Where-Object { $ExcludeDirNames -contains $_ }).Count -gt 0) {
            continue
        }
        $targetDir = Join-Path $DestinationDir $relativeDir
        New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    }

    $allFiles = Get-ChildItem -LiteralPath $SourceDir -Recurse -File -Force
    foreach ($file in $allFiles) {
        $relativeFile = $file.FullName.Substring($sourceRoot.Length).TrimStart('\')
        $segments = $relativeFile -split '[\\/]'
        if ($SkipPyCache -and ($segments -contains "__pycache__")) {
            continue
        }
        if (($segments | Where-Object { $ExcludeDirNames -contains $_ }).Count -gt 0) {
            continue
        }
        $skipFile = $false
        foreach ($pattern in $ExcludeFilePatterns) {
            if ($file.Name -like $pattern) {
                $skipFile = $true
                break
            }
        }
        if ($skipFile) {
            continue
        }
        $targetFile = Join-Path $DestinationDir $relativeFile
        $targetParent = Split-Path -Parent $targetFile
        if (-not (Test-Path $targetParent)) {
            New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
        }
        Copy-Item -LiteralPath $file.FullName -Destination $targetFile -Force
    }
}

function Remove-DirectoryContentsIfExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return
    }

    Get-ChildItem -LiteralPath $Path -Force | ForEach-Object {
        if ($_.PSIsContainer) {
            Remove-PathBestEffort -Path $_.FullName -Recurse
        } else {
            Remove-PathBestEffort -Path $_.FullName
        }
    }
}

function Get-PythonRuntimeInfo {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonExe,
        [Parameter(Mandatory = $true)]
        [string]$TempRoot
    )

    $scriptPath = Join-Path $TempRoot ("python-runtime-info-" + [Guid]::NewGuid().ToString("N") + ".py")
    $scriptContent = @'
import json
import sys

print(json.dumps({
    "base_prefix": sys.base_prefix,
    "major": sys.version_info.major,
    "minor": sys.version_info.minor,
}))
'@

    Set-Content -LiteralPath $scriptPath -Value $scriptContent -Encoding ASCII
    try {
        $json = & $PythonExe $scriptPath
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to query Python runtime info."
        }
        return $json | ConvertFrom-Json
    } finally {
        Remove-PathBestEffort -Path $scriptPath
    }
}

function New-StdlibZip {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BasePythonDir,
        [Parameter(Mandatory = $true)]
        [string]$StdlibZipPath,
        [Parameter(Mandatory = $true)]
        [string]$StageRoot
    )

    $stdlibSource = Join-Path $BasePythonDir "Lib"
    $stdlibStage = Join-Path $StageRoot "stdlib"
    Remove-PathBestEffort -Path $stdlibStage -Recurse
    New-Item -ItemType Directory -Path $stdlibStage -Force | Out-Null

    Copy-DirectoryContentsFiltered `
        -SourceDir $stdlibSource `
        -DestinationDir $stdlibStage `
        -ExcludeDirNames @("site-packages", "test", "tests", "idlelib", "ensurepip", "tkinter", "turtledemo", "venv", "__pycache__") `
        -ExcludeFilePatterns @("*.pyc", "*.pyo") `
        -SkipPyCache

    if (Test-Path $StdlibZipPath) {
        Remove-PathWithRetry -Path $StdlibZipPath
    }
    Compress-DirectoryWithRetry -SourceDir $stdlibStage -DestinationZip $StdlibZipPath
    Remove-PathBestEffort -Path $stdlibStage -Recurse
}

function Install-DependenciesToTarget {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonExe,
        [Parameter(Mandatory = $true)]
        [string]$RequirementsFile,
        [Parameter(Mandatory = $true)]
        [string]$TargetDir
    )

    Remove-DirectoryContentsIfExists -Path $TargetDir
    New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null

    & $PythonExe -m pip install `
        --disable-pip-version-check `
        --no-compile `
        --upgrade `
        --target $TargetDir `
        -r $RequirementsFile

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install runtime dependencies."
    }
}

function New-VendorZip {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SitePackagesDir,
        [Parameter(Mandatory = $true)]
        [string]$VendorZipPath,
        [Parameter(Mandatory = $true)]
        [string]$StageRoot,
        [string[]]$IncludeDirs = @(),
        [string[]]$IncludeFiles = @()
    )

    $vendorStage = Join-Path $StageRoot "vendor"
    Remove-PathBestEffort -Path $vendorStage -Recurse
    New-Item -ItemType Directory -Path $vendorStage -Force | Out-Null

    foreach ($dirName in $IncludeDirs) {
        $sourceDir = Join-Path $SitePackagesDir $dirName
        if (-not (Test-Path $sourceDir -PathType Container)) {
            continue
        }
        $targetDir = Join-Path $vendorStage $dirName
        Copy-DirectoryContentsFiltered `
            -SourceDir $sourceDir `
            -DestinationDir $targetDir `
            -ExcludeDirNames @("__pycache__") `
            -ExcludeFilePatterns @("*.pyc", "*.pyo") `
            -SkipPyCache
        Remove-PathBestEffort -Path $sourceDir -Recurse
    }

    foreach ($fileName in $IncludeFiles) {
        $sourceFile = Join-Path $SitePackagesDir $fileName
        if (-not (Test-Path $sourceFile -PathType Leaf)) {
            continue
        }
        Copy-Item -LiteralPath $sourceFile -Destination (Join-Path $vendorStage $fileName) -Force
        Remove-PathBestEffort -Path $sourceFile
    }

    if ((Get-ChildItem $vendorStage -Recurse -File | Measure-Object).Count -eq 0) {
        Remove-PathBestEffort -Path $vendorStage -Recurse
        return
    }

    if (Test-Path $VendorZipPath) {
        Remove-PathWithRetry -Path $VendorZipPath
    }
    Compress-DirectoryWithRetry -SourceDir $vendorStage -DestinationZip $VendorZipPath
    Remove-PathBestEffort -Path $vendorStage -Recurse
}

function Remove-PathsByName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootDir,
        [Parameter(Mandatory = $true)]
        [string[]]$DirectoryNames
    )

    if (-not (Test-Path $RootDir)) {
        return
    }

    Get-ChildItem -LiteralPath $RootDir -Recurse -Directory -Force | ForEach-Object {
        if ($DirectoryNames -contains $_.Name) {
            Remove-PathBestEffort -Path $_.FullName -Recurse
        }
    }
}

function Remove-FilesByPattern {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootDir,
        [Parameter(Mandatory = $true)]
        [string[]]$Patterns
    )

    if (-not (Test-Path $RootDir)) {
        return
    }

    foreach ($pattern in $Patterns) {
        Get-ChildItem -LiteralPath $RootDir -Recurse -File -Force -Filter $pattern | ForEach-Object {
            Remove-PathBestEffort -Path $_.FullName
        }
    }
}

function Prune-RuntimeDependencies {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SitePackagesDir
    )

    if (-not (Test-Path $SitePackagesDir)) {
        return
    }

    Remove-PathsByName -RootDir $SitePackagesDir -DirectoryNames @(
        "__pycache__",
        "tests",
        "test",
        "testing",
        "docs",
        "doc",
        "examples",
        "example",
        "licenses"
    )

    Remove-FilesByPattern -RootDir $SitePackagesDir -Patterns @(
        "*.pyc",
        "*.pyo"
    )

    Get-ChildItem -LiteralPath $SitePackagesDir -Recurse -File -Force | ForEach-Object {
        if (
            $_.Name -in @("RECORD", "INSTALLER", "REQUESTED", "direct_url.json") -or
            $_.FullName -like "*.dist-info\WHEEL"
        ) {
            Remove-PathBestEffort -Path $_.FullName
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
$BundledPythonDir = Join-Path $FullDir "python"
$BundledSitePackagesDir = Join-Path $BundledPythonDir "Lib\site-packages"

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
New-Item -ItemType Directory -Path $BundledSitePackagesDir -Force | Out-Null

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

$PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$PythonInfo = Get-PythonRuntimeInfo -PythonExe $PythonExe -TempRoot $TempRoot
$BasePythonDir = $PythonInfo.base_prefix
$PythonTag = "python{0}{1}" -f $PythonInfo.major, $PythonInfo.minor

$RuntimeRootFiles = @(
    "python.exe",
    "pythonw.exe",
    "python3.dll",
    "$PythonTag.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "LICENSE.txt"
)

foreach ($fileName in $RuntimeRootFiles) {
    $sourceFile = Join-Path $BasePythonDir $fileName
    if (Test-Path $sourceFile) {
        Copy-Item -LiteralPath $sourceFile -Destination (Join-Path $BundledPythonDir $fileName) -Force
    }
}

$DllSourceDir = Join-Path $BasePythonDir "DLLs"
if (Test-Path $DllSourceDir) {
    Copy-Item -LiteralPath $DllSourceDir -Destination (Join-Path $BundledPythonDir "DLLs") -Recurse -Force
}

New-StdlibZip -BasePythonDir $BasePythonDir -StdlibZipPath (Join-Path $BundledPythonDir "$PythonTag.zip") -StageRoot $StageRoot
Install-DependenciesToTarget -PythonExe $PythonExe -RequirementsFile (Join-Path $ProjectRoot "requirements.txt") -TargetDir $BundledSitePackagesDir
Prune-RuntimeDependencies -SitePackagesDir $BundledSitePackagesDir
New-VendorZip `
    -SitePackagesDir $BundledSitePackagesDir `
    -VendorZipPath (Join-Path $BundledPythonDir "vendor.zip") `
    -StageRoot $StageRoot `
    -IncludeDirs @(
        "pyrogram",
        "aiogram",
        "fastapi",
        "starlette",
        "anyio",
        "uvicorn",
        "magic_filter",
        "aiofiles",
        "aiosqlite",
        "annotated_doc",
        "annotated_types",
        "click",
        "h11",
        "multipart",
        "pyaes",
        "python_socks",
        "aiohttp_socks",
        "typing_inspection"
    ) `
    -IncludeFiles @(
        "typing_extensions.py",
        "socks.py",
        "sockshandler.py"
    )

$PthContent = @"
$PythonTag.zip
DLLs
vendor.zip
Lib\site-packages
..\app
"@
Set-Content -LiteralPath (Join-Path $BundledPythonDir "$PythonTag._pth") -Value $PthContent -Encoding ASCII

$StartBat = @"
@echo off
setlocal
cd /d "%~dp0app"
..\python\python.exe main.py
"@
Set-Content -LiteralPath (Join-Path $FullDir "start.bat") -Value $StartBat -Encoding ASCII

$StartNoBrowserBat = @"
@echo off
setlocal
cd /d "%~dp0app"
set TG_CHANNEL_SYNC_NO_BROWSER=1
..\python\python.exe main.py
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
