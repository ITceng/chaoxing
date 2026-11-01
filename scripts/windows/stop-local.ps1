$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Initialize-LocalRuntimeDirectories
$pidFile = Join-Path $script:RuntimeDir 'backend.pid'
$stopFile = Join-Path $script:RuntimeDir 'stop.request'

try {
    if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
        $health = Test-HttpEndpoint -Uri 'http://127.0.0.1:8088/api/v1/health/ready'
        if ($health.Ok) {
            Write-LocalLog -Level WARN -Message '8088 健康检查可达，但没有本项目 PID 记录；为避免终止其他进程，未停止任何服务。'
        } else {
            Write-LocalLog '本项目未运行；未停止 PostgreSQL，也未更改数据库。'
        }
        return
    }

    $processInfo = Get-OwnedBackendProcess
    if (-not $processInfo) {
        $rawPid = (Get-Content -LiteralPath $pidFile -Raw).Trim()
        $pidValue = 0
        if ([int]::TryParse($rawPid, [ref]$pidValue) -and -not (Get-CimInstance Win32_Process -Filter "ProcessId = $pidValue" -ErrorAction SilentlyContinue)) {
            Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue
            Write-LocalLog 'PID 记录对应的进程已退出；已清理陈旧 PID/停止标记，未影响其他进程。'
            return
        }
        Write-LocalLog -Level ERROR -Message 'PID 对应进程无法确认是本项目后端；出于安全考虑不写停止请求、不终止进程。请人工检查 PID 文件和进程。'
        exit 1
    }

    $ownedPid = [int]$processInfo.ProcessId
    Set-Content -LiteralPath $stopFile -Value $ownedPid -Encoding ASCII
    Write-LocalLog "已发送 PID 匹配的正常停止请求（PID=$ownedPid）。不会强制结束进程，也不会停止 PostgreSQL。"
    $stopped = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        $remaining = Get-CimInstance Win32_Process -Filter "ProcessId = $ownedPid" -ErrorAction SilentlyContinue
        if (-not $remaining) { $stopped = $true; break }
        $expectedPython = Join-Path $script:RepoRoot '.venv\Scripts\python.exe'
        if ($remaining.CommandLine -notmatch 'unified_system\.local_server' -or -not $remaining.ExecutablePath -or [System.IO.Path]::GetFullPath($remaining.ExecutablePath) -ne [System.IO.Path]::GetFullPath($expectedPython)) {
            throw 'PID 已被其他进程复用；立即停止等待，不对该进程执行任何操作。'
        }
    }
    if ($stopped) {
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue
        Write-LocalLog '后端已正常退出。PostgreSQL 服务保持原状态，数据库和日志均保留。'
    } else {
        Write-LocalLog -Level ERROR -Message "后端尚未确认退出；未强制结束。停止请求保留在 $stopFile。检查 $($script:LogDir) 后再处理。"
        exit 1
    }
} catch {
    Write-LocalLog -Level ERROR -Message $_.Exception.Message
    exit 1
}
