$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Initialize-LocalRuntimeDirectories
$python = Join-Path $script:RepoRoot '.venv\Scripts\python.exe'
$frontend = Join-Path $script:UnifiedRoot 'frontend'
$pidFile = Join-Path $script:RuntimeDir 'backend.pid'
$runStamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
$stdoutLog = Join-Path $script:LogDir "backend.$runStamp.stdout.log"
$stderrLog = Join-Path $script:LogDir "backend.$runStamp.stderr.log"
$buildLog = Join-Path $script:LogDir "frontend-build.$runStamp.log"
$baseUrl = 'http://127.0.0.1:8088'
$process = $null
$scriptExitCode = 0

function Invoke-FrontendBuildIfNeeded {
    $viteEntry = Join-Path $frontend 'node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $viteEntry -PathType Leaf)) {
        throw "前端依赖未安装。首次安装一次即可：cd `"$frontend`"; npm ci。不会在每次启动时重复安装。"
    }
    $distIndex = Join-Path $frontend 'dist\index.html'
    $sourcePaths = @(
        (Join-Path $frontend 'src'),
        (Join-Path $frontend 'index.html'),
        (Join-Path $frontend 'vite.config.ts'),
        (Join-Path $frontend 'tsconfig.json'),
        (Join-Path $frontend 'tsconfig.app.json'),
        (Join-Path $frontend 'tsconfig.node.json'),
        (Join-Path $frontend 'postcss.config.js'),
        (Join-Path $frontend 'tailwind.config.js'),
        (Join-Path $frontend 'public'),
        (Join-Path $frontend '.env'),
        (Join-Path $frontend '.env.local'),
        (Join-Path $frontend '.env.production'),
        (Join-Path $frontend '.env.production.local'),
        (Join-Path $frontend 'package.json'),
        (Join-Path $frontend 'package-lock.json')
    )
    $latestSource = [DateTime]::MinValue
    foreach ($path in $sourcePaths) {
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $items = if ((Get-Item -LiteralPath $path).PSIsContainer) { Get-ChildItem -LiteralPath $path -Recurse -File } else { @(Get-Item -LiteralPath $path) }
        foreach ($item in $items) { if ($item.LastWriteTimeUtc -gt $latestSource) { $latestSource = $item.LastWriteTimeUtc } }
    }
    $needsBuild = -not (Test-Path -LiteralPath $distIndex -PathType Leaf)
    if (-not $needsBuild -and $latestSource -gt (Get-Item -LiteralPath $distIndex).LastWriteTimeUtc) { $needsBuild = $true }
    if ($needsBuild) {
        Write-LocalLog '前端构建产物缺失或已过期；运行 npm run build（不安装/重装依赖）。'
        Push-Location $frontend
        try {
            & npm.cmd run build 2>&1 | Tee-Object -FilePath $buildLog
            $buildExit = $LASTEXITCODE
        } finally { Pop-Location }
        if ($buildExit -ne 0 -or -not (Test-Path -LiteralPath $distIndex -PathType Leaf)) {
            throw "前端构建失败，退出码=$buildExit；详见 $buildLog。"
        }
    } else {
        Write-LocalLog '前端静态构建已是最新；启动时不重新安装依赖或重复构建。'
    }
}

try {
    Write-LocalLog '开始本机 Windows 启动预检。'
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($git) {
        $branch = (& git.exe -C $script:RepoRoot branch --show-current 2>$null | Select-Object -First 1)
        $commit = (& git.exe -C $script:RepoRoot rev-parse --short=12 HEAD 2>$null | Select-Object -First 1)
        $dirty = @(& git.exe -C $script:RepoRoot status --porcelain=v1 2>$null).Count
        Write-LocalLog "Git branch=$branch commit=$commit changed_or_untracked=$dirty"
    } else {
        Write-LocalLog '未找到 Git；继续检查运行依赖。' 'WARN'
    }

    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "仓库虚拟环境不存在：$python。请按 docs/local-windows-runbook.md 一次性创建 .venv 并安装后端依赖。"
    }
    $pythonVersion = (& $python --version 2>&1 | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0) { throw '仓库 .venv Python 无法运行。' }
    Write-LocalLog "Python: $pythonVersion（使用仓库 .venv）。"
    & $python -c 'import flask, sqlalchemy, psycopg, alembic, argon2, cryptography, werkzeug'
    if ($LASTEXITCODE -ne 0) { throw '后端依赖不完整；请按 runbook 在 .venv 中安装 unified_system/requirements.txt。' }

    $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $nodeCommand -or -not $npmCommand) { throw '未找到 Node.js 或 npm。请手动安装 Node.js LTS；启动器不会安装系统软件。' }
    $nodeVersion = (& node.exe --version 2>&1 | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0) { throw 'Node.js 无法运行。' }
    $npmVersion = (& npm.cmd --version 2>&1 | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0) { throw 'npm 无法运行。' }
    Write-LocalLog "Node.js=$nodeVersion npm=$npmVersion"
    Invoke-FrontendBuildIfNeeded

    Import-LocalSettings
    Push-Location $script:RepoRoot
    try {
        & $python -m flask --app unified_system.app:create_app verify-credential-keyring
        if ($LASTEXITCODE -ne 0) { throw '凭据加密密钥环合成自检失败；未启动后端。' }
    } finally { Pop-Location }
    $service = Start-PostgresService -ServiceName $env:POSTGRES_SERVICE_NAME
    $port = [int]$env:LOCAL_DATABASE_PORT
    Write-LocalLog "检查本机 PostgreSQL TCP 端口 127.0.0.1:$port。"
    $tcp = Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue
    if (-not $tcp.TcpTestSucceeded) { throw "PostgreSQL 服务 $($service.Name) 已运行，但 127.0.0.1:$port 未监听。请检查该服务的端口配置。" }

    if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
        $previousPid = 0
        $previousPidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
        if ([int]::TryParse($previousPidText, [ref]$previousPid) -and $previousPid -gt 0) {
            $previousProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $previousPid" -ErrorAction SilentlyContinue
            if ($previousProcess -and -not (Get-OwnedBackendProcess)) {
                throw "PID 文件指向一个无法确认归属的现存进程（PID=$previousPid）；不会覆盖记录或终止进程。请人工检查。"
            }
            if (-not $previousProcess) {
                Remove-Item -LiteralPath $pidFile -Force
                Write-LocalLog '清理了已退出进程的陈旧 PID 记录；未影响其他进程。'
            }
        } else {
            throw 'PID 文件格式无效；不会覆盖它或终止任何进程，请人工检查。'
        }
    }
    $owned = Get-OwnedBackendProcess
    $listeners = @(Get-ListeningPortInfo -Port 8088)
    if ($owned -and $listeners.Count -gt 0) {
        $health = Test-HttpEndpoint -Uri "$baseUrl/api/v1/health/ready"
        if ($health.Ok) {
            Write-LocalLog "本项目后端已在运行且健康；不会重复启动。地址：$baseUrl/"
            try { Start-Process "$baseUrl/" }
            catch { Write-LocalLog -Level WARN -Message "服务健康但无法自动打开默认浏览器；请手动访问 $baseUrl/。" }
            return
        }
        throw '检测到本项目后端进程，但健康检查未通过；请查阅后端日志，停止脚本不会终止未知进程。'
    }
    if ($owned) { throw '检测到本项目 PID 仍存在但 8088 未监听；为避免重复启动，请先运行停止项目.bat 并查看日志。' }
    if ($listeners.Count -gt 0) {
        $listenerText = ($listeners | ForEach-Object { "pid=$($_.OwningProcess) address=$($_.LocalAddress)" }) -join '; '
        throw "端口 8088 已被其他进程占用（$listenerText）；不会结束或覆盖该进程。请关闭占用程序或联系管理员调整端口。"
    }

    Write-LocalLog '检查 PostgreSQL 连接和 Alembic 当前 revision；若版本不匹配将停止，不会自动迁移或回退。'
    & $python (Join-Path $PSScriptRoot 'check_local_db.py')
    if ($LASTEXITCODE -ne 0) {
        throw "数据库连接失败或 Alembic revision 不等于项目 head。请先备份，再按 runbook 运行 scripts\windows\initialize-local-db.ps1。日志：$($script:LogDir)"
    }

    $stopFilePath = Join-Path $script:RuntimeDir 'stop.request'
    Remove-Item -LiteralPath $stopFilePath -Force -ErrorAction SilentlyContinue
    $previousRuntime = $env:UNIFIED_LOCAL_RUNTIME_DIR
    $previousStop = $env:UNIFIED_LOCAL_STOP_FILE
    $previousPort = $env:UNIFIED_LOCAL_PORT
    try {
        $env:UNIFIED_LOCAL_RUNTIME_DIR = $script:RuntimeDir
        $env:UNIFIED_LOCAL_STOP_FILE = Join-Path $script:RuntimeDir 'stop.request'
        $env:UNIFIED_LOCAL_PORT = '8088'
        $process = Start-Process -FilePath $python -ArgumentList @('-m', 'unified_system.local_server') `
            -WorkingDirectory $script:RepoRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    } finally {
        $env:UNIFIED_LOCAL_RUNTIME_DIR = $previousRuntime
        $env:UNIFIED_LOCAL_STOP_FILE = $previousStop
        $env:UNIFIED_LOCAL_PORT = $previousPort
    }
    Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ASCII

    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        $process.Refresh()
        if ($process.HasExited) { break }
        $health = Test-HttpEndpoint -Uri "$baseUrl/api/v1/health/ready"
        if ($health.Ok) { $ready = $true; break }
    }
    if (-not $ready) {
        $process.Refresh()
        if (-not $process.HasExited -and (Get-OwnedBackendProcess)) {
            Set-Content -LiteralPath $stopFilePath -Value $process.Id -Encoding ASCII
            for ($i = 0; $i -lt 20; $i++) {
                Start-Sleep -Milliseconds 500
                $process.Refresh()
                if ($process.HasExited) { break }
            }
        }
        if ($process.HasExited) { Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue }
        $tail = if (Test-Path $stderrLog) { (Get-Content $stderrLog -Tail 15) -join [Environment]::NewLine } else { '无 stderr 日志' }
        throw "后端未在 20 秒内通过数据库就绪检查。未打开浏览器。日志：$stderrLog`n$tail"
    }

    $startedAt = (Get-Date).ToString('o')
    $metadata = [ordered]@{
        pid = $process.Id
        started_at = $startedAt
        branch = $branch
        commit = $commit
        backend_url = $baseUrl
        frontend_mode = 'built static assets served by Flask; same-origin; no separate frontend process'
        postgres_service = $service.Name
        postgres_version = Get-PostgresVersionForService (Resolve-PostgresService -ServiceName $service.Name)
        stdout_log = $stdoutLog
        stderr_log = $stderrLog
        build_log = $buildLog
    }
    $metadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $script:RuntimeDir 'runtime.json') -Encoding UTF8
    Write-LocalLog "启动成功。前端与后端同源地址：$baseUrl/；健康检查：$baseUrl/api/v1/health/ready；PID=$($process.Id)。"
    Write-LocalLog "日志目录：$($script:LogDir)。停止时只关闭本项目后端，不停止 PostgreSQL。"
    try { Start-Process "$baseUrl/" }
    catch { Write-LocalLog -Level WARN -Message "服务健康但无法自动打开默认浏览器；请手动访问 $baseUrl/。" }
} catch {
    try { Write-LocalLog -Level ERROR -Message $_.Exception.Message } catch { Write-Host $_.Exception.Message -ForegroundColor Red }
    $scriptExitCode = 1
} finally {
    Restore-LocalSettingsEnvironment
}
if ($scriptExitCode -ne 0) { exit $scriptExitCode }
