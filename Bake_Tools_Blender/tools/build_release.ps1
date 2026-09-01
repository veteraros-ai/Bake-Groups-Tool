param(
    [string]$OutputDirectory = (Join-Path ([Environment]::GetFolderPath('Desktop')) "Bake_Tools_Blender_Releases"),
    [string]$VendorSource = "",
    [string]$PythonExecutable = "python",
    [ValidateSet("All", "Standard", "Superhive")]
    [string]$Channel = "All"
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
$BuildStandard = $Channel -in @("All", "Standard")
$BuildSuperhive = $Channel -in @("All", "Superhive")

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$OutputDirectory = (Resolve-Path $OutputDirectory).Path
$Archive = Join-Path $OutputDirectory "Bake_Tools_Blender-$Version-win64.zip"
$SuperhiveArchive = Join-Path $OutputDirectory "Bake_Groups_Tool_Blender_${Version}_Superhive_Windows_x64.zip"
if ($BuildStandard -and (Test-Path -LiteralPath $Archive)) {
    Remove-Item -LiteralPath $Archive -Force
}
if ($BuildSuperhive -and (Test-Path -LiteralPath $SuperhiveArchive)) {
    Remove-Item -LiteralPath $SuperhiveArchive -Force
}

$TempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$StageRoot = [IO.Path]::GetFullPath((Join-Path $TempRoot ("BakeToolsRelease_" + [Guid]::NewGuid().ToString("N"))))
if (-not $StageRoot.StartsWith($TempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe release staging path: $StageRoot"
}
$StageAddon = Join-Path $StageRoot "Bake_Tools_Blender"
$StageVendor = Join-Path $StageAddon "addon\bake_tools_blender\vendor"

function Write-ReleaseFiles([string]$AddonRoot) {
    $releaseFiles = Get-ChildItem -LiteralPath $AddonRoot -Recurse -File |
        ForEach-Object { $_.FullName.Substring($AddonRoot.Length + 1).Replace("\", "/") } |
        Sort-Object
    $releaseFiles | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $AddonRoot "release_files.json") -Encoding UTF8
}

function Write-ArchiveHash([string]$ArchivePath) {
    $hash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$ArchivePath.sha256" `
        -Value "$hash  $([IO.Path]::GetFileName($ArchivePath))" -Encoding ASCII
    return $hash
}

try {
    New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null
    Copy-Item -LiteralPath $ProjectRoot -Destination $StageAddon -Recurse

    foreach ($directoryName in @("tools", "__pycache__", "vendor", "graphify-out")) {
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
    $StageDocs = Join-Path $StageAddon "docs"
    if (Test-Path -LiteralPath $StageDocs) {
        Get-ChildItem -LiteralPath $StageDocs -File -Filter "RELEASE_NOTES_1.*.md" |
            Where-Object { $_.Name -ne "RELEASE_NOTES_1.0.0.md" } |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
    }

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

    # PySide6 wheels contain developer launchers and Qt authoring tools.  The
    # add-on uses QtCore/QtGui/QtWidgets directly and never executes these files.
    # Remove them from every public channel, including the ordinary GitHub ZIP.
    Get-ChildItem -LiteralPath $StageVendor -File -Filter "*.exe" -Recurse |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

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
        "PySide6\plugins\platforms\qwindows.dll",
        "shiboken6\Shiboken.pyd"
    )
    foreach ($relative in $RequiredVendorFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $StageVendor $relative))) {
            throw "Bundled runtime is incomplete: $relative"
        }
    }
    $ExecutableFiles = @(Get-ChildItem -LiteralPath $StageAddon -File -Filter "*.exe" -Recurse)
    if ($ExecutableFiles) {
        throw "Executable remained in the standard release: $($ExecutableFiles[0].FullName)"
    }

    $LocalManifest = Join-Path $StageAddon "update_manifest.json"
    if (Test-Path -LiteralPath $LocalManifest) {
        $ManifestData = Get-Content -LiteralPath $LocalManifest -Raw | ConvertFrom-Json
        $ManifestData.package_sha256 = ""
        $ManifestData | ConvertTo-Json -Depth 8 |
            Set-Content -LiteralPath $LocalManifest -Encoding UTF8
    }
    Write-ReleaseFiles $StageAddon

    if ($BuildStandard) {
        Compress-Archive -LiteralPath $StageAddon -DestinationPath $Archive -CompressionLevel Optimal
        $Hash = Write-ArchiveHash $Archive
        Write-Host "Standard release package: $Archive"
        Write-Host "Standard SHA-256: $Hash"
    }

    if ($BuildSuperhive) {
        $SuperhiveStageRoot = Join-Path $StageRoot "superhive"
        $SuperhiveAddon = Join-Path $SuperhiveStageRoot "Bake_Tools_Blender"
        New-Item -ItemType Directory -Force -Path $SuperhiveStageRoot | Out-Null
        Copy-Item -LiteralPath $StageAddon -Destination $SuperhiveAddon -Recurse

        # Preserve the artist manual but remove developer-facing documentation.
        $SuperhiveAssets = Join-Path $SuperhiveAddon "assets"
        Copy-Item -LiteralPath (Join-Path $SuperhiveAddon "docs\Manual.pur") `
            -Destination (Join-Path $SuperhiveAssets "Manual.pur") -Force
        Remove-Item -LiteralPath (Join-Path $SuperhiveAddon "docs") -Recurse -Force

        # Physically remove every external update/rollback and telemetry module.
        $SuperhiveRemove = @(
            "addon\bake_tools_blender\about_update.py",
            "addon\bake_tools_blender\update_service.py",
            "addon\bake_tools_blender\telemetry.py",
            "addon\bake_tools_blender\localization\publication_overrides.json",
            "update_manifest.json",
            "PRIVACY.md",
            "SECURITY.md"
        )
        foreach ($relative in $SuperhiveRemove) {
            $path = Join-Path $SuperhiveAddon $relative
            if (Test-Path -LiteralPath $path) {
                Remove-Item -LiteralPath $path -Force
            }
        }
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot "superhive_root_init.py") `
            -Destination (Join-Path $SuperhiveAddon "__init__.py") -Force
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot "superhive_release_channel.py") `
            -Destination (Join-Path $SuperhiveAddon "addon\bake_tools_blender\release_channel.py") -Force
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot "SUPERHIVE_README.md") `
            -Destination (Join-Path $SuperhiveAddon "README.md") -Force

        $SuperhiveManifestPath = Join-Path $SuperhiveAddon "blender_manifest.toml"
        $SuperhiveManifest = Get-Content -LiteralPath $SuperhiveManifestPath |
            Where-Object { $_ -notmatch '^network\s*=' }
        $SuperhiveManifest = $SuperhiveManifest -replace `
            '^website\s*=.*$', 'website = "https://superhivemarket.com/"'
        $SuperhiveManifest |
            Set-Content -LiteralPath $SuperhiveManifestPath -Encoding UTF8

        Write-ReleaseFiles $SuperhiveAddon

        $SuperhiveExecutables = @(
            Get-ChildItem -LiteralPath $SuperhiveAddon -File -Filter "*.exe" -Recurse
        )
        if ($SuperhiveExecutables) {
            throw "Executable remained in Superhive release: $($SuperhiveExecutables[0].FullName)"
        }
        $ForbiddenSuperhiveFiles = @(
            "addon\bake_tools_blender\about_update.py",
            "addon\bake_tools_blender\update_service.py",
            "addon\bake_tools_blender\telemetry.py",
            "addon\bake_tools_blender\localization\publication_overrides.json",
            "update_manifest.json",
            "docs"
        )
        foreach ($relative in $ForbiddenSuperhiveFiles) {
            if (Test-Path -LiteralPath (Join-Path $SuperhiveAddon $relative)) {
                throw "Forbidden Superhive content remained: $relative"
            }
        }

        $SuperhiveInstructions = Join-Path $SuperhiveStageRoot "INSTALLATION.txt"
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot "SUPERHIVE_INSTALLATION.txt") `
            -Destination $SuperhiveInstructions -Force
        Compress-Archive -LiteralPath $SuperhiveAddon,$SuperhiveInstructions `
            -DestinationPath $SuperhiveArchive -CompressionLevel Optimal
        $SuperhiveHash = Write-ArchiveHash $SuperhiveArchive
        Write-Host "Superhive package: $SuperhiveArchive"
        Write-Host "Superhive SHA-256: $SuperhiveHash"
    }
}
finally {
    if ((Test-Path -LiteralPath $StageRoot) -and $StageRoot.StartsWith($TempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $StageRoot -Recurse -Force
    }
}
