param(
    [string]$BlenderExe = "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
    [string]$PythonInclude = "",
    [string]$PythonLib = "",
    [string]$PybindInclude = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$NativeDir = Join-Path $ProjectRoot "addon\bake_tools_blender\native"
$Source = Join-Path $NativeDir "bg_math_core_blender.cpp"
$Target = Join-Path $NativeDir "bg_math_core_blender.pyd"

if (-not (Test-Path -LiteralPath $Source)) {
    throw "Native source not found: $Source"
}
if (-not (Test-Path -LiteralPath $BlenderExe)) {
    throw "Blender executable not found: $BlenderExe"
}

$probe = & $BlenderExe --background --factory-startup --python-expr `
    "import sys; print('BT_PYTHON=' + str(sys.version_info.major) + str(sys.version_info.minor))" 2>&1
$pythonTag = (($probe | Select-String "BT_PYTHON=(\d+)").Matches.Groups[1].Value | Select-Object -First 1)
if (-not $pythonTag) {
    throw "Could not determine Blender Python ABI"
}

if (-not $PythonInclude) {
    $includeCandidates = @(
        "C:\Program Files\Blender Foundation\Blender 5.1\5.1\python\Include",
        "C:\Program Files\Autodesk\Maya2027\include\Python313\Python",
        "C:\Program Files\Adobe\Adobe Substance 3D Designer\plugins\pythonsdk\include",
        "C:\Program Files\Adobe\Adobe Substance 3D Painter_ver_12.03\resources\pythonsdk\include"
    )
    $PythonInclude = $includeCandidates |
        Where-Object { Test-Path -LiteralPath (Join-Path $_ "Python.h") } |
        Select-Object -First 1
}
if (-not $PythonLib) {
    $libCandidates = @(
        "C:\Program Files\Blender Foundation\Blender 5.1\5.1\python\libs\python$pythonTag.lib",
        "C:\Program Files\Autodesk\Maya2027\lib\python$pythonTag.lib",
        "C:\Program Files\Adobe\Adobe Substance 3D Designer\plugins\pythonsdk\libs\python$pythonTag.lib",
        "C:\Program Files\Adobe\Adobe Substance 3D Painter_ver_12.03\resources\pythonsdk\libs\python$pythonTag.lib"
    )
    $PythonLib = $libCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $PybindInclude) {
    $pybindCandidates = @(
        (Join-Path $ProjectRoot "third_party\pybind11\include"),
        (Join-Path $env:USERPROFILE "Documents\maya\scripts\Bake_Groups\pybind11-master\include")
    )
    $PybindInclude = $pybindCandidates |
        Where-Object { Test-Path -LiteralPath (Join-Path $_ "pybind11\pybind11.h") } |
        Select-Object -First 1
}

if (-not $PythonInclude -or -not (Test-Path -LiteralPath (Join-Path $PythonInclude "Python.h"))) {
    throw "CPython $pythonTag headers not found. Pass -PythonInclude <folder containing Python.h>."
}
if (-not $PythonLib -or -not (Test-Path -LiteralPath $PythonLib)) {
    throw "python$pythonTag.lib not found. Pass -PythonLib <full path>."
}
if (-not $PybindInclude -or -not (Test-Path -LiteralPath (Join-Path $PybindInclude "pybind11\pybind11.h"))) {
    throw "pybind11 headers not found. Pass -PybindInclude <pybind11 include folder>."
}

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path -LiteralPath $vswhere) {
    $vsRoot = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
}
$vsDevCmd = if ($vsRoot) { Join-Path $vsRoot "Common7\Tools\VsDevCmd.bat" } else { "" }
if (-not $vsDevCmd -or -not (Test-Path -LiteralPath $vsDevCmd)) {
    $vsDevCmd = Get-ChildItem -LiteralPath "C:\Program Files\Microsoft Visual Studio" `
        -Recurse -Filter "VsDevCmd.bat" -File -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $vsDevCmd -or -not (Test-Path -LiteralPath $vsDevCmd)) {
    throw "Visual Studio C++ build tools were not found"
}

$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$tempDir = [IO.Path]::GetFullPath((Join-Path $tempBase ("BakeToolsNative_" + [Guid]::NewGuid().ToString("N"))))
if (-not $tempDir.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe native build directory: $tempDir"
}

try {
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
    $tempTarget = Join-Path $tempDir "bg_math_core_blender.pyd"
    $pythonLibDir = Split-Path -Parent $PythonLib
    $pythonLibName = Split-Path -Leaf $PythonLib
    $compile = @(
        ('call "{0}" -no_logo -arch=x64 -host_arch=x64' -f $vsDevCmd),
        ('cl.exe /nologo /std:c++17 /O2 /EHsc /MD /LD /DNDEBUG /DBG_MATH_CORE_MODULE_NAME=bg_math_core_blender /I"{0}" /I"{1}" "{2}" /link /LIBPATH:"{3}" "{4}" /OUT:"{5}"' -f `
            $PythonInclude, $PybindInclude, $Source, $pythonLibDir, $pythonLibName, $tempTarget)
    ) -join ' && '
    & $env:ComSpec /d /s /c $compile
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $tempTarget)) {
        throw "Native compiler failed with exit code $LASTEXITCODE"
    }
    Copy-Item -LiteralPath $tempTarget -Destination $Target -Force

    $escapedNative = $NativeDir.Replace("'", "''")
    & $BlenderExe --background --factory-startup --python-expr `
        "import sys; sys.path.insert(0, r'$escapedNative'); import bg_math_core_blender as c; print('BAKE_TOOLS_NATIVE_OK', c.__version__, c.host, c.calculate_avg_distance([0,0,0],[1,0,0]))"
    if ($LASTEXITCODE -ne 0) {
        throw "Blender could not import the compiled native module"
    }
    Write-Host "Native module: $Target"
}
finally {
    if ((Test-Path -LiteralPath $tempDir) -and $tempDir.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $tempDir -Recurse -Force
    }
}
