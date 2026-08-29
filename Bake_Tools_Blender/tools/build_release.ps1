param(
    [string]$OutputDirectory = (Join-Path ([Environment]::GetFolderPath('Desktop')) "Bake_Tools_Blender_Releases"),
    [string]$VendorSource = "",
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ManifestPath = Join-Path $ProjectRoot "blender_manifest.toml"
if (-not (Test-Path -LiteralPath $ManifestPath)) {
    throw "Not a Blender extension source root: $ProjectRoot"
}
$ManifestText = Get-Content -LiteralPath $ManifestPath -Raw
$VersionMatch = [regex]::Match($ManifestText, '(?m)^version\s*=\s*"([^"]+)"')
if (-not $VersionMatch.Success) {
    throw "Version not found in blender_manifest.toml"
}
$Version = $VersionMatch.Groups[1].Value

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$OutputDirectory = (Resolve-Path $OutputDirectory).Path
$Archive = Join-Path $OutputDirectory "Bake_Tools_Blender-$Version-win64.zip"
$MarketplaceArchive = Join-Path $OutputDirectory "Bake_Groups_Tool_Blender_${Version}_Marketplace_Windows_x64.zip"
if (Test-Path -LiteralPath $Archive) {
    Remove-Item -LiteralPath $Archive -Force
}
if (Test-Path -LiteralPath $MarketplaceArchive) {
    Remove-Item -LiteralPath $MarketplaceArchive -Force
}

$TempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$StageRoot = [IO.Path]::GetFullPath((Join-Path $TempRoot ("BakeToolsRelease_" + [Guid]::NewGuid().ToString("N"))))
if (-not $StageRoot.StartsWith($TempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe release staging path: $StageRoot"
}
$StageAddon = Join-Path $StageRoot "Bake_Tools_Blender"
$StageVendor = Join-Path $StageAddon "addon\bake_tools_blender\vendor"

try {
    New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null
    Copy-Item -LiteralPath $ProjectRoot -Destination $StageAddon -Recurse

    foreach ($directoryName in @("tools", "__pycache__", "vendor")) {
        Get-ChildItem -LiteralPath $StageAddon -Directory -Filter $directoryName -Recurse |
            Sort-Object { $_.FullName.Length } -Descending |
            ForEach-Object {
                $resolved = [IO.Path]::GetFullPath($_.FullName)
                if ($resolved.StartsWith($StageAddon, [StringComparison]::OrdinalIgnoreCase)) {
                    Remove-Item -LiteralPath $resolved -Recurse -Force
                }
            }
    }
    Get-ChildItem -LiteralPath $StageAddon -File -Recurse |
        Where-Object { $_.Extension -in @(".pyc", ".obj", ".lib", ".exp") } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
    Get-ChildItem -LiteralPath (Join-Path $StageAddon "docs") -File -Filter "RELEASE_NOTES_1.*.md" |
        Where-Object { $_.Name -ne "RELEASE_NOTES_1.0.0.md" } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

    New-Item -ItemType Directory -Force -Path $StageVendor | Out-Null
    if ($VendorSource -and (Test-Path -LiteralPath $VendorSource)) {
        Get-ChildItem -LiteralPath (Resolve-Path $VendorSource).Path -Force |
            Copy-Item -Destination $StageVendor -Recurse -Force
    }
    else {
        & $PythonExecutable (Join-Path $PSScriptRoot "prepare_vendor.py") --output $StageVendor
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to prepare bundled PySide6 runtime"
        }
    }

    # An already-installed vendor tree may contain import caches created by
    # Blender. Clean again after injection so artist packages stay immutable.
    Get-ChildItem -LiteralPath $StageVendor -Directory -Filter "__pycache__" -Recurse |
        Sort-Object { $_.FullName.Length } -Descending |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
    Get-ChildItem -LiteralPath $StageVendor -File -Recurse |
        Where-Object { $_.Extension -in @(".pyc", ".obj", ".lib", ".exp") } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

    $RequiredVendorFiles = @(
        "PySide6\QtCore.pyd", "PySide6\QtGui.pyd", "PySide6\QtWidgets.pyd",
        "PySide6\Qt6Core.dll", "PySide6\Qt6Gui.dll", "PySide6\Qt6Widgets.dll",
        "shiboken6\Shiboken.pyd"
    )
    foreach ($relative in $RequiredVendorFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $StageVendor $relative))) {
            throw "Bundled runtime is incomplete: $relative"
        }
    }

    $LocalManifest = Join-Path $StageAddon "update_manifest.json"
    if (Test-Path -LiteralPath $LocalManifest) {
        $ManifestData = Get-Content -LiteralPath $LocalManifest -Raw | ConvertFrom-Json
        $ManifestData.package_sha256 = ""
        $ManifestData | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $LocalManifest -Encoding UTF8
    }

    $ReleaseFiles = Get-ChildItem -LiteralPath $StageAddon -Recurse -File |
        ForEach-Object { $_.FullName.Substring($StageAddon.Length + 1).Replace("\", "/") } |
        Sort-Object
    $ReleaseFiles | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $StageAddon "release_files.json") -Encoding UTF8

    Compress-Archive -LiteralPath $StageAddon -DestinationPath $Archive -CompressionLevel Optimal
    $Hash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$Archive.sha256" -Value "$Hash  $([IO.Path]::GetFileName($Archive))" -Encoding ASCII

    $MarketplaceInstructions = Join-Path $StageRoot "INSTALLATION.txt"
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "MARKETPLACE_INSTALLATION.txt") `
        -Destination $MarketplaceInstructions -Force
    Compress-Archive -LiteralPath $StageAddon,$MarketplaceInstructions `
        -DestinationPath $MarketplaceArchive -CompressionLevel Optimal
    $MarketplaceHash = (Get-FileHash -LiteralPath $MarketplaceArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$MarketplaceArchive.sha256" `
        -Value "$MarketplaceHash  $([IO.Path]::GetFileName($MarketplaceArchive))" -Encoding ASCII

    Write-Host "Release package: $Archive"
    Write-Host "SHA-256: $Hash"
    Write-Host "Marketplace package: $MarketplaceArchive"
    Write-Host "Marketplace SHA-256: $MarketplaceHash"
}
finally {
    if ((Test-Path -LiteralPath $StageRoot) -and $StageRoot.StartsWith($TempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $StageRoot -Recurse -Force
    }
}
