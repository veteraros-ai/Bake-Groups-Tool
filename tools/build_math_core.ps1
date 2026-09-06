param(
    [string[]]$Versions = @("2022", "2023", "2024", "2025", "2026", "2027"),
    [string]$Config = "Release",
    [string]$Generator = "",
    [string]$Architecture = "x64",
    [string]$DevkitsRoot = "C:\Maya_Devkits",
    [string]$Pybind11SourceDir = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

function Invoke-Native {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}

foreach ($version in $Versions) {
    foreach ($moduleName in @("bg_math_core", "bg_math_core_runtime")) {
        $buildDir = Join-Path $repoRoot "build\${moduleName}_$version"
        $cmakeArgs = @(
            "-S", $repoRoot,
            "-B", $buildDir,
            "-DMAYA_VERSION=$version",
            "-DDEVKITS_ROOT=$DevkitsRoot",
            "-DBG_MATH_CORE_MODULE_NAME=$moduleName"
        )

        if ($Generator) {
            $cmakeArgs += @("-G", $Generator)
            if ($Architecture) {
                $cmakeArgs += @("-A", $Architecture)
            }
        }

        if ($Pybind11SourceDir) {
            $cmakeArgs += "-DPYBIND11_SOURCE_DIR=$Pybind11SourceDir"
        }

        Write-Host "Configuring $moduleName for Maya $version"
        Invoke-Native -FilePath "cmake" -Arguments $cmakeArgs

        Write-Host "Building $moduleName for Maya $version"
        Invoke-Native -FilePath "cmake" -Arguments @("--build", $buildDir, "--config", $Config, "--target", "bg_math_core")

        $outFile = Join-Path $repoRoot "Bake_Groups\bin\$version\$moduleName.pyd"
        if (-not (Test-Path -LiteralPath $outFile)) {
            throw "Build finished but output was not found: $outFile"
        }

        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $outFile).Hash
        Write-Host "Built $outFile"
        Write-Host "SHA256 $hash"
    }

    $runtimeDir = Join-Path $repoRoot "Bake_Groups\bin\$version\runtime"
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
    $sourceFile = Join-Path $repoRoot "Bake_Groups\bin\$version\bg_math_core_runtime.pyd"
    $targetFile = Join-Path $runtimeDir "bg_math_core_runtime.pyd"
    try {
        Copy-Item -LiteralPath $sourceFile -Destination $targetFile -Force
        Write-Host "Installed $targetFile"
    }
    catch {
        $pendingFile = "$targetFile.pending"
        Copy-Item -LiteralPath $sourceFile -Destination $pendingFile -Force
        Write-Warning "Module is in use; staged update for next Maya start: $pendingFile"
    }
    Remove-Item -LiteralPath $sourceFile -Force
}
