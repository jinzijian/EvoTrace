$ErrorActionPreference = "Stop"

$EvoTraceSource = if ($env:EVOTRACE_INSTALL_SOURCE) {
    $env:EVOTRACE_INSTALL_SOURCE
} else {
    "git+https://github.com/jinzijian/evotrace.git"
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "EvoTrace needs Git. Install Git, then run this installer again."
}

Write-Host "Installing EvoTrace..."

if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv tool install --force $EvoTraceSource
} elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
    pipx install --force $EvoTraceSource
} else {
    $Python = Get-Command py -ErrorAction SilentlyContinue
    if (-not $Python) {
        $Python = Get-Command python -ErrorAction SilentlyContinue
    }
    if (-not $Python) {
        throw "Install uv, pipx, or Python 3.9+, then run this installer again."
    }

    & $Python.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw "EvoTrace needs Python 3.9 or newer."
    }

    $InstallRoot = if ($env:EVOTRACE_INSTALL_ROOT) {
        $env:EVOTRACE_INSTALL_ROOT
    } else {
        Join-Path $env:LOCALAPPDATA "EvoTrace"
    }
    $Venv = Join-Path $InstallRoot "venv"
    & $Python.Source -m venv $Venv
    $VenvPython = Join-Path $Venv "Scripts\python.exe"
    & $VenvPython -m pip install --upgrade $EvoTraceSource

    $BinDir = Join-Path $env:USERPROFILE ".local\bin"
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $VenvEvoTrace = Join-Path $Venv "Scripts\evotrace.exe"
    Set-Content -Path (Join-Path $BinDir "evotrace.cmd") -Value "@`"$VenvEvoTrace`" %*"
    Set-Content -Path (Join-Path $BinDir "et.cmd") -Value "@`"$VenvEvoTrace`" %*"

    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($UserPath -split ";") -notcontains $BinDir) {
        $UpdatedPath = if ($UserPath) { "$UserPath;$BinDir" } else { $BinDir }
        [Environment]::SetEnvironmentVariable("Path", $UpdatedPath, "User")
        Write-Host "Added $BinDir to your user PATH. Open a new terminal before running EvoTrace."
    }
}

Write-Host ""
Write-Host "EvoTrace installed. Build your local asset index with:"
Write-Host "  evotrace init"
Write-Host ""
Write-Host "No session data was read during installation."
