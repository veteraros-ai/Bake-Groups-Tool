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
    $buildDir = Join-Path $repoRoot "build\bg_math_core_$version"
    $cmakeArgs = @(
        "-S", $repoRoot,
        "-B", $buildDir,
        "-DMAYA_VERSION=$version",
        "-DDEVKITS_ROOT=$DevkitsRoot"
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

    Write-Host "Configuring bg_math_core for Maya $version"
    Invoke-Native -FilePath "cmake" -Arguments $cmakeArgs

    Write-Host "Building bg_math_core for Maya $version"
    Invoke-Native -FilePath "cmake" -Arguments @("--build", $buildDir, "--config", $Config, "--target", "bg_math_core")

    $outFile = Join-Path $repoRoot "Bake_Groups\bin\$version\bg_math_core.pyd"
    if (-not (Test-Path $outFile)) {
        throw "Build finished but output was not found: $outFile"
    }

    $hash = (Get-FileHash -Algorithm SHA256 $outFile).Hash
    Write-Host "Built $outFile"
    Write-Host "SHA256 $hash"
}
