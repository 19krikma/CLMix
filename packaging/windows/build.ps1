# Builds a double-click Windows installer for CLMix.
#
# Run this ON WINDOWS (PyInstaller bundles the interpreter of whatever OS
# it runs on - it cannot cross-compile a Windows .exe from Linux/macOS).
#
# Prerequisites:
#   - Python 3.10+ from python.org (NOT the Microsoft Store package - it's
#     missing pieces Tkinter needs), with "Add python.exe to PATH" checked.
#   - Inno Setup 6: https://jrsoftware.org/isdl.php (default install path
#     is auto-detected below; installs ISCC.exe, the compiler)
#
# Usage (from anywhere):
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
#
# Produces packaging\windows\dist_installer\CLMixSetup-<version>.exe

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")

Set-Location $RepoRoot

Write-Host "==> Reading version from version.py"
$versionLine = Select-String -Path "version.py" -Pattern '^VERSION = "(.+)"$'
if (-not $versionLine) {
    throw "Could not find VERSION in version.py"
}
$Version = $versionLine.Matches[0].Groups[1].Value
Write-Host "    Version: $Version"

Write-Host "==> Setting up build virtual environment (.venv-build)"
if (-not (Test-Path ".venv-build")) {
    python -m venv .venv-build
}
$VenvPython = Join-Path $RepoRoot ".venv-build\Scripts\python.exe"

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt pyinstaller

Write-Host "==> Running PyInstaller"
& $VenvPython -m PyInstaller "packaging\windows\clmix.spec" `
    --distpath dist --workpath build --noconfirm

if (-not (Test-Path "dist\CLMix\CLMix.exe")) {
    throw "PyInstaller did not produce dist\CLMix\CLMix.exe"
}

Write-Host "==> Locating Inno Setup compiler (ISCC.exe)"
$Iscc = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
if (-not $Iscc) {
    $candidates = @(
        "${Env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$Env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $found) {
        throw "ISCC.exe not found. Install Inno Setup 6 from https://jrsoftware.org/isdl.php"
    }
    $IsccPath = $found
} else {
    $IsccPath = $Iscc.Source
}

Write-Host "==> Building installer with Inno Setup"
& $IsccPath "/DMyAppVersion=$Version" "packaging\windows\installer.iss"

$OutputExe = "packaging\windows\dist_installer\CLMixSetup-$Version.exe"
Write-Host ""
Write-Host "==> Done: $OutputExe"
