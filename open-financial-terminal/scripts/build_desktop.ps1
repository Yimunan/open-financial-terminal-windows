<#
.SYNOPSIS
  Build the Open Financial Terminal desktop distributable.

.DESCRIPTION
  1. Builds the React SPA (frontend/dist).
  2. Freezes the FastAPI backend + qhfi engine into a onedir app via PyInstaller
     (dist_desktop/oft-backend/oft-backend.exe). The frozen app serves the SPA and opens its own
     WebView2 window — a complete standalone desktop app.
  3. Optionally freezes the standalone qhfi.exe CLI (-Qhfi).
  4. Optionally compiles the Windows installer with Inno Setup if installed (-Installer).

.PARAMETER Console
  Build a DEBUG backend with a console window + live tracebacks (sets OFT_CONSOLE=1).
  Omit for the release (windowed) build that logs to %LOCALAPPDATA%\OpenFinancialTerminal\logs.

.PARAMETER Qhfi
  Also build the standalone qhfi.exe CLI from the sibling qhfi repo.

.PARAMETER Installer
  Also compile packaging\oft-installer.iss with Inno Setup (ISCC.exe must be available).

.PARAMETER Sign
  Authenticode-sign the produced exe(s) + installer with signtool. Configure the cert via env vars:
    OFT_SIGN_THUMBPRINT  — SHA1 thumbprint of a cert in the user/machine store, OR
    OFT_SIGN_PFX + OFT_SIGN_PFX_PASSWORD — path + password of a .pfx
    OFT_SIGN_TIMESTAMP   — RFC3161 timestamp URL (default: http://timestamp.digicert.com)
  Without a configured cert, signing is skipped with a warning (build still succeeds).

.PARAMETER Version
  Installer version (passed to Inno Setup as /DAppVersion). Omit to use the default
  #define AppVersion in packaging\oft-installer.iss.

.EXAMPLE
  pwsh scripts\build_desktop.ps1                 # release windowed app
  pwsh scripts\build_desktop.ps1 -Console        # debuggable build
  pwsh scripts\build_desktop.ps1 -Qhfi -Installer
  pwsh scripts\build_desktop.ps1 -Installer -Version 1.0.3
  $env:OFT_SIGN_THUMBPRINT='...'; pwsh scripts\build_desktop.ps1 -Installer -Sign
#>
param(
  [switch]$Console,
  [switch]$Qhfi,
  [switch]$Installer,
  [switch]$Sign,
  [string]$Version
)
$ErrorActionPreference = "Stop"

$Root      = Split-Path -Parent $PSScriptRoot           # project root
$Backend   = Join-Path $Root "backend"
$Frontend  = Join-Path $Root "frontend"
$Venv      = Join-Path $Backend ".venv\Scripts"
$PyInst    = Join-Path $Venv "pyinstaller.exe"
$Qhfime    = Join-Path (Split-Path -Parent $Root) "quant-hedge-fund-incubator"

function Invoke-Sign($path) {
  # Authenticode-sign one file when -Sign is set and a cert is configured; otherwise a safe no-op.
  if (-not $Sign) { return }
  $st = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like '*\x64\*' } | Sort-Object FullName -Descending | Select-Object -First 1
  if (-not $st) { Write-Warning "signtool not found (install Windows SDK); skipping $path"; return }
  $ts = if ($env:OFT_SIGN_TIMESTAMP) { $env:OFT_SIGN_TIMESTAMP } else { "http://timestamp.digicert.com" }
  if ($env:OFT_SIGN_THUMBPRINT) {
    & $st.FullName sign /sha1 $env:OFT_SIGN_THUMBPRINT /fd SHA256 /tr $ts /td SHA256 $path
  } elseif ($env:OFT_SIGN_PFX) {
    & $st.FullName sign /f $env:OFT_SIGN_PFX /p $env:OFT_SIGN_PFX_PASSWORD /fd SHA256 /tr $ts /td SHA256 $path
  } else {
    Write-Warning "No signing cert configured (set OFT_SIGN_THUMBPRINT or OFT_SIGN_PFX[/_PASSWORD]); skipping $path"
    return
  }
  if ($LASTEXITCODE -ne 0) { throw "signtool failed for $path : $LASTEXITCODE" }
  Write-Host "    signed: $path" -ForegroundColor Green
}

Write-Host "==> [1/4] Building frontend (npm run build)" -ForegroundColor Cyan
Push-Location $Frontend
try {
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "npm run build failed (tsc/vite): $LASTEXITCODE" }
} finally { Pop-Location }

Write-Host "==> [2/4] Freezing backend (PyInstaller)" -ForegroundColor Cyan
if ($Console) { $env:OFT_CONSOLE = "1" } else { Remove-Item Env:\OFT_CONSOLE -ErrorAction SilentlyContinue }
Push-Location $Backend
try {
  & $PyInst "oft-backend.spec" --noconfirm --distpath dist_desktop --workpath build_desktop
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller (backend) failed: $LASTEXITCODE" }
} finally { Pop-Location }
Invoke-Sign (Join-Path $Backend "dist_desktop\oft-backend\oft-backend.exe")
Write-Host "    -> $Backend\dist_desktop\oft-backend\oft-backend.exe" -ForegroundColor Green

if ($Qhfi) {
  Write-Host "==> [3/4] Freezing qhfi.exe CLI" -ForegroundColor Cyan
  Push-Location $Qhfime
  try {
    & $PyInst "qhfi-cli.spec" --noconfirm --distpath dist_desktop --workpath build_desktop
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller (qhfi) failed: $LASTEXITCODE" }
  } finally { Pop-Location }
  Invoke-Sign (Join-Path $Qhfime "dist_desktop\qhfi\qhfi.exe")
  # Zip the standalone CLI onedir for distribution (it has no installer of its own).
  $zip = Join-Path $Root "packaging\dist_installer\qhfi-cli-windows-x64.zip"
  New-Item -ItemType Directory -Force (Split-Path $zip) | Out-Null
  if (Test-Path $zip) { Remove-Item $zip -Force }
  Compress-Archive -Path (Join-Path $Qhfime "dist_desktop\qhfi\*") -DestinationPath $zip
  Write-Host "    -> $Qhfime\dist_desktop\qhfi\qhfi.exe  (+ $zip)" -ForegroundColor Green
} else {
  Write-Host "==> [3/4] Skipping qhfi.exe (pass -Qhfi to build it)" -ForegroundColor DarkGray
}

if ($Installer) {
  Write-Host "==> [4/4] Compiling installer (Inno Setup)" -ForegroundColor Cyan
  $iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"   # winget user-scope install
  ) | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $iscc) { $iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source }
  if (-not $iscc) { throw "Inno Setup not found. Install it: winget install JRSoftware.InnoSetup" }
  $isccArgs = @()
  if ($Version) { $isccArgs += "/DAppVersion=$Version" }
  $isccArgs += (Join-Path $Root "packaging\oft-installer.iss")
  & $iscc @isccArgs
  if ($LASTEXITCODE -ne 0) { throw "ISCC failed: $LASTEXITCODE" }
  $setup = Get-ChildItem (Join-Path $Root "packaging\dist_installer") -Filter "OpenFinancialTerminal-Setup-*.exe" |
           Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $setup) { throw "Inno Setup produced no OpenFinancialTerminal-Setup-*.exe in packaging\dist_installer" }
  Invoke-Sign $setup.FullName
  Write-Host "    -> $Root\packaging\dist_installer\" -ForegroundColor Green
} else {
  Write-Host "==> [4/4] Skipping installer (pass -Installer to compile it)" -ForegroundColor DarkGray
}

Write-Host "`nDone." -ForegroundColor Green
