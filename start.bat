@echo off
setlocal
set "SHEETMATE_START_FILE=%~f0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$source = Get-Content -LiteralPath $env:SHEETMATE_START_FILE -Raw -Encoding UTF8; & ([scriptblock]::Create(($source -split '(?m)^# SHEETMATE_POWERSHELL\r?$', 2)[1]))"
set "SHEETMATE_EXIT=%errorlevel%"
if not "%SHEETMATE_EXIT%"=="0" pause
exit /b %SHEETMATE_EXIT%
# SHEETMATE_POWERSHELL
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$OutputEncoding = [Console]::OutputEncoding

function Invoke-Step([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command failed (exit $LASTEXITCODE)." }
}

try {
    $root = Split-Path -Parent $env:SHEETMATE_START_FILE
    Set-Location -LiteralPath $root
    Write-Host '=== SheetMate ==='
    if (-not (Test-Path -LiteralPath '.env')) {
        Copy-Item -LiteralPath '.env.example' -Destination '.env'
        Write-Host '已生成 .env；模型服务请在登录后的设置页配置。'
    }

    # 与后端一致：环境变量 > 根目录 .env > backend/.env；空值使用 8000。
    $found = Test-Path Env:PORT
    $portText = $env:PORT
    foreach ($file in @('.env', 'backend\.env')) {
        if ($found -or -not (Test-Path -LiteralPath $file)) { continue }
        foreach ($line in Get-Content -LiteralPath $file -Encoding UTF8) {
            if ($line -match '^\s*(?:export\s+)?PORT\s*=\s*(.*?)\s*$') {
                $found = $true
                $portText = $Matches[1]
            }
        }
    }
    if ($portText -match '^"([^"]*)"\s*(?:#.*)?$' -or $portText -match "^'([^']*)'\s*(?:#.*)?$") {
        $portText = $Matches[1]
    } else {
        $portText = ([string]$portText -replace '(?:^|\s)#.*$', '').Trim()
    }
    $portText = ([string]$portText).Trim()
    $port = 8000
    if (-not [string]::IsNullOrWhiteSpace($portText)) {
        if ($portText -notmatch '^\d+$' -or -not [int]::TryParse($portText, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
            throw 'PORT 必须是 1 到 65535 的整数。'
        }
    }
    $env:PORT = [string]$port
    $ports = @($port, 5173) | Sort-Object -Unique
    function Get-StartupListeners {
        @(Get-NetTCPConnection -ErrorAction Stop | Where-Object { $_.State -eq 'Listen' -and $ports -contains $_.LocalPort })
    }
    $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    Write-Host "清理启动端口：$($ports -join ', ')"
    foreach ($processId in @(Get-StartupListeners | Select-Object -ExpandProperty OwningProcess -Unique)) {
        & $taskkill /PID $processId /T /F
    }
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $remaining = @(Get-StartupListeners)
        if ($remaining.Count -eq 0) { break }
        Start-Sleep -Milliseconds 100
    }
    if ($remaining.Count -gt 0) {
        throw "端口仍被占用：$($remaining.LocalPort -join ', ')。请关闭对应程序，或以管理员身份重试。"
    }

    $backend = Join-Path $root 'backend'
    $python = Join-Path $backend '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "未找到项目虚拟环境 Python：$python"
    }
    Invoke-Step $python @('-c', 'import sys; assert sys.version_info >= (3, 10), sys.version')
    Set-Location -LiteralPath $backend
    Write-Host '安装后端依赖…'
    $pipReady = $false
    try { & $python -m pip --version *> $null; $pipReady = $LASTEXITCODE -eq 0 } catch {}
    if (-not $pipReady) { Invoke-Step $python @('-m', 'ensurepip', '--upgrade') }
    Invoke-Step $python @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $backend 'requirements.txt'))
    Set-Location -LiteralPath (Join-Path $root 'frontend')
    $npm = (Get-Command npm.cmd -CommandType Application -ErrorAction Stop).Source
    Write-Host '安装前端依赖…'
    Invoke-Step $npm @('install', '--no-audit', '--no-fund')
    Write-Host '构建前端…'
    Invoke-Step $npm @('run', 'build')
    Set-Location -LiteralPath $backend
    Write-Host "打开 http://127.0.0.1:$port   默认管理员 admin / admin123   (Ctrl+C 停止)"
    Invoke-Step $python @('-m', 'sheetmate')
    exit 0
} catch {
    Write-Host "启动失败：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
