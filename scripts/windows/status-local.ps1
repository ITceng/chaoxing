$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Initialize-LocalRuntimeDirectories

function Read-LocalSettingValue {
    param([string]$Name)
    if (-not (Test-Path -LiteralPath $script:SettingsFile -PathType Leaf)) { return $null }
    $line = Get-Content -LiteralPath $script:SettingsFile | Where-Object { $_.StartsWith("$Name=") } | Select-Object -First 1
    if ($line) { return $line.Substring($Name.Length + 1) }
    return $null
}

Write-Host '=== 本地项目状态 ==='
$git = Get-Command git.exe -ErrorAction SilentlyContinue
if ($git) {
    $branch = (& git.exe -C $script:RepoRoot branch --show-current 2>$null | Select-Object -First 1)
    $commit = (& git.exe -C $script:RepoRoot rev-parse --short=12 HEAD 2>$null | Select-Object -First 1)
    $dirty = @(& git.exe -C $script:RepoRoot status --porcelain=v1 2>$null).Count
    Write-Host "Git：branch=$branch commit=$commit changes=$dirty"
} else { Write-Host 'Git：未安装/不在 Git 仓库' }

$python = Join-Path $script:RepoRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $python) {
    $pythonVersion = (& $python --version 2>&1 | Select-Object -First 1)
    Write-Host "Python venv：$pythonVersion"
} else { Write-Host 'Python venv：缺失' }
if (Get-Command node.exe -ErrorAction SilentlyContinue) { Write-Host "Node.js：$(& node.exe --version 2>&1 | Select-Object -First 1)" } else { Write-Host 'Node.js：未安装' }
if (Get-Command npm.cmd -ErrorAction SilentlyContinue) { Write-Host "npm：$(& npm.cmd --version 2>&1 | Select-Object -First 1)" } else { Write-Host 'npm：未安装' }

$serviceName = Read-LocalSettingValue 'POSTGRES_SERVICE_NAME'
$services = @(Get-PostgresServices)
if ($serviceName) { $services = @($services | Where-Object { $_.Name -eq $serviceName }) }
if ($services.Count -eq 0) {
    Write-Host 'PostgreSQL：未检测到已配置服务'
} else {
    foreach ($service in $services) {
        $windowsService = Get-Service -Name $service.Name -ErrorAction SilentlyContinue
        $version = Get-PostgresVersionForService $service
        Write-Host "PostgreSQL：$($service.Name) 状态=$($windowsService.Status) 版本=$version"
    }
}
$dbPortText = Read-LocalSettingValue 'LOCAL_DATABASE_PORT'
$dbPort = 0
if ([int]::TryParse($dbPortText, [ref]$dbPort)) {
    $dbListeners = @(Get-ListeningPortInfo -Port $dbPort)
    if ($dbListeners.Count -gt 0) { Write-Host "PostgreSQL 端口 127.0.0.1:$dbPort：LISTEN" }
    else { Write-Host "PostgreSQL 端口 127.0.0.1:$dbPort：未监听" }
} else { Write-Host 'PostgreSQL 端口：未配置' }
if ((Test-Path -LiteralPath $script:SettingsFile -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $script:RepoRoot '.venv\Scripts\python.exe'))) {
    try {
        Import-LocalSettings
        $dbOutput = @(& (Join-Path $script:RepoRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'check_local_db.py') 2>&1)
        $dbExit = $LASTEXITCODE
        $dbResult = ($dbOutput -join '') | ConvertFrom-Json
        if ($dbResult.ok) {
            $revisionStatus = if ($dbResult.migration_current) { 'Alembic revision=当前 head' } else { "Alembic revision 不匹配；current=$($dbResult.current_revisions -join ',') expected=$($dbResult.expected_heads -join ',')" }
            Write-Host "PostgreSQL 连接：正常；数据库=$($dbResult.database)；server_version=$($dbResult.postgres_version)；$revisionStatus"
        } else { Write-Host "PostgreSQL 连接：失败（$($dbResult.error_type)$($dbResult.reason)）" }
    } catch { Write-Host 'PostgreSQL 连接：未检查（配置或驱动不可用）' }
    finally { Restore-LocalSettingsEnvironment }
} else { Write-Host 'PostgreSQL 连接：未检查（尚未配置或 .venv 缺失）' }

$backendPidFile = Join-Path $script:RuntimeDir 'backend.pid'
$owned = Get-OwnedBackendProcess
if ($owned) {
    $health = Test-HttpEndpoint -Uri 'http://127.0.0.1:8088/api/v1/health/ready'
    $home = Test-HttpEndpoint -Uri 'http://127.0.0.1:8088/'
    $healthText = if ($health.Ok) { '健康' } else { '进程存在但健康检查失败' }
    $frontendText = if ($home.Ok) { 'HTTP 可访问（Flask 提供已构建 Vue 静态页）' } else { '不可访问' }
    Write-Host "后端：PID=$($owned.ProcessId) $healthText"
    Write-Host "前端：$frontendText；地址 http://127.0.0.1:8088/（无独立 Vite 进程）"
} elseif (Test-Path -LiteralPath $backendPidFile) {
    Write-Host '后端：PID 文件陈旧或与本项目不匹配；未尝试终止任何进程'
    Write-Host '前端：未知；请检查端口和日志'
} else {
    $health = Test-HttpEndpoint -Uri 'http://127.0.0.1:8088/api/v1/health/ready'
    if ($health.Ok) { Write-Host '后端：8088 有健康响应，但不是由本项目 PID 记录确认' }
    else { Write-Host '后端：未运行' }
    Write-Host '前端：未运行'
}
$backendListeners = @(Get-ListeningPortInfo -Port 8088)
if ($backendListeners.Count -gt 0) {
    $owners = ($backendListeners | ForEach-Object { "pid=$($_.OwningProcess)@$($_.LocalAddress):$($_.LocalPort)" }) -join ', '
    Write-Host "监听端口：$owners"
} else { Write-Host '监听端口：8088 未监听' }

Write-Host 'Worker：Chaoxing=禁用/未验证；Zhihuishu=禁用/未验证；Yinghua=禁用/未接入。未启动 Worker。'
$metadataPath = Join-Path $script:RuntimeDir 'runtime.json'
if (Test-Path -LiteralPath $metadataPath) {
    try {
        $metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
        Write-Host "最近启动时间：$($metadata.started_at)"
        if ($metadata.stderr_log) { Write-Host "后端 stderr：$($metadata.stderr_log)" }
        if ($metadata.stdout_log) { Write-Host "后端 stdout：$($metadata.stdout_log)" }
        if ($metadata.build_log) { Write-Host "前端构建日志：$($metadata.build_log)" }
    } catch { Write-Host '最近启动时间：运行元数据不可解析' }
} else { Write-Host '最近启动时间：暂无记录' }
Write-Host "日志目录：$($script:LogDir)"
Write-Host 'PostgreSQL 不会被停止脚本关闭；数据库和日志保留。'
