$ErrorActionPreference = "Stop"

$EvoTraceSource = if ($env:EVOTRACE_INSTALL_SOURCE) {
    $env:EVOTRACE_INSTALL_SOURCE
} else {
    "https://github.com/jinzijian/EvoTrace.git"
}
$InstallRoot = if ($env:EVOTRACE_INSTALL_ROOT) {
    $env:EVOTRACE_INSTALL_ROOT
} else {
    Join-Path $env:LOCALAPPDATA "EvoTrace\app"
}
$BinDir = if ($env:EVOTRACE_BIN_DIR) {
    $env:EVOTRACE_BIN_DIR
} else {
    Join-Path $env:USERPROFILE ".local\bin"
}

foreach ($Requirement in @(
    @{ Command = "git"; Label = "Git" },
    @{ Command = "node"; Label = "Node.js 22.19+ or 24+" }
)) {
    if (-not (Get-Command $Requirement.Command -ErrorAction SilentlyContinue)) {
        throw "EvoTrace needs $($Requirement.Label). Install it, then run this installer again."
    }
}

$Python = Get-Command py -ErrorAction SilentlyContinue
if (-not $Python) {
    $Python = Get-Command python -ErrorAction SilentlyContinue
}
if (-not $Python) {
    throw "EvoTrace needs Python 3.9+. Install it, then run this installer again."
}

node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit(a>=24||(a===22&&b>=19)?0:1)'
if ($LASTEXITCODE -ne 0) {
    throw "EvoTrace needs Node.js 22.19+ or 24+. Found $(node --version)."
}
& $Python.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "EvoTrace needs Python 3.9 or newer."
}

Write-Host "Installing the EvoTrace DeepSeek Harness distribution..."

if (Test-Path (Join-Path $InstallRoot ".git")) {
    git -C $InstallRoot pull --ff-only
} elseif (Test-Path $InstallRoot) {
    throw "$InstallRoot exists but is not an EvoTrace Git checkout. Choose an empty EVOTRACE_INSTALL_ROOT."
} else {
    $Parent = Split-Path -Parent $InstallRoot
    New-Item -ItemType Directory -Force -Path $Parent | Out-Null
    git clone --depth 1 $EvoTraceSource $InstallRoot
}

foreach ($Required in @("package.json", "pnpm-lock.yaml", "harness\launcher.mjs")) {
    if (-not (Test-Path (Join-Path $InstallRoot $Required) -PathType Leaf)) {
        throw "EvoTrace release payload is incomplete: missing $Required. The GitHub revision must include the Harness application."
    }
}

$Venv = Join-Path $InstallRoot ".venv"
& $Python.Source -m venv $Venv
$VenvPython = Join-Path $Venv "Scripts\python.exe"
& $VenvPython -m pip install --quiet --upgrade $InstallRoot

Push-Location $InstallRoot
try {
    if (Get-Command pnpm -ErrorAction SilentlyContinue) {
        pnpm install --prod --frozen-lockfile
    } elseif (Get-Command corepack -ErrorAction SilentlyContinue) {
        corepack pnpm install --prod --frozen-lockfile
    } elseif (Get-Command npx -ErrorAction SilentlyContinue) {
        npx --yes pnpm@10.30.1 install --prod --frozen-lockfile
    } else {
        throw "EvoTrace needs pnpm, Corepack, or npx to install DeepSeek Harness."
    }
} finally {
    Pop-Location
}

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$Node = (Get-Command node).Source
$Launcher = Join-Path $InstallRoot "harness\launcher.mjs"
$Command = "@`"$Node`" `"$Launcher`" %*"
Set-Content -Path (Join-Path $BinDir "evotrace.cmd") -Value $Command
Set-Content -Path (Join-Path $BinDir "et.cmd") -Value $Command
& $Node $Launcher setup

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($UserPath -split ";") -notcontains $BinDir) {
    $UpdatedPath = if ($UserPath) { "$UserPath;$BinDir" } else { $BinDir }
    [Environment]::SetEnvironmentVariable("Path", $UpdatedPath, "User")
    Write-Host "Added $BinDir to your user PATH. Open a new terminal before running EvoTrace."
}

Write-Host ""
Write-Host "EvoTrace installed. Launch the Harness app with:"
Write-Host "  evotrace"
Write-Host ""
Write-Host "Inside the app, type / and run /init when you are ready to index local history."
Write-Host "No session data was read during installation."
