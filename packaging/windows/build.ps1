# Builds dist\eecc-redact-setup-<version>.exe and its .sha256.
# Needs uv and Inno Setup 6 (https://jrsoftware.org/isinfo.php).
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

$Version = (uv run python -c "import eecc_redact; print(eecc_redact.__version__)").Trim()
Write-Host "Building eecc-redact $Version"

# The icon is drawn with Qt; no window is needed for that.
$Platform = $env:QT_QPA_PLATFORM
$env:QT_QPA_PLATFORM = "offscreen"
uv run python packaging\icon.py build\eecc-redact.ico
if ($LASTEXITCODE -ne 0) { throw "Drawing the icon failed" }
$env:QT_QPA_PLATFORM = $Platform

uv run --group build pyinstaller --noconfirm --clean `
    --distpath dist --workpath build packaging\eecc-redact.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$Candidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
)
$Iscc = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { $Iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source }
if (-not $Iscc) { throw "Inno Setup 6 was not found. Install it from https://jrsoftware.org/isinfo.php" }

& $Iscc "/DAppVersion=$Version" packaging\windows\eecc-redact.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

$Name = "eecc-redact-setup-$Version.exe"
$Installer = Join-Path $Root "dist\$Name"
$Hash = (Get-FileHash -Algorithm SHA256 $Installer).Hash.ToLower()
Set-Content -NoNewline -Encoding ascii -Path "$Installer.sha256" -Value "$Hash  $Name"
Write-Host "Built $Installer"
Write-Host "SHA-256 $Hash"
