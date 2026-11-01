$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Initialize-LocalRuntimeDirectories
$python = Join-Path $script:RepoRoot '.venv\Scripts\python.exe'
$scriptExitCode = 0

try {
    Import-LocalSettings
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw '找不到 .venv\Scripts\python.exe；请先按 runbook 创建虚拟环境并安装依赖。' }
    if (Get-OwnedBackendProcess) { throw '请先停止本项目后端，再执行数据库迁移；不会在服务运行时修改 schema。' }
    $null = Start-PostgresService -ServiceName $env:POSTGRES_SERVICE_NAME

    & $python (Join-Path $PSScriptRoot 'check_local_db.py')
    $checkCode = $LASTEXITCODE
    if ($checkCode -eq 3) { throw '数据库连接失败；请核对服务状态、端口、数据库名、用户名和密码。' }
    if ($checkCode -eq 2) { throw '本地数据库配置不是匹配的本机 PostgreSQL（scheme/host/数据库名/端口）；拒绝迁移。' }
    if ($checkCode -notin @(0, 4)) { throw "数据库版本预检异常（exit=$checkCode）；拒绝迁移。" }

    $databaseName = $env:LOCAL_DATABASE_NAME
    if (-not $databaseName) { $databaseName = '(配置数据库)' }
    Write-Host ''
    Write-Host '即将只执行 Alembic upgrade head（仅向前迁移）。不会执行 downgrade、drop、清空或重建数据库。'
    Write-Host '如果数据库已有重要数据，请先按 docs/local-windows-runbook.md 完成并核验 pg_dump 备份。'
    $backupConfirmation = Read-Host "已有数据输入 BACKUP_VERIFIED:$databaseName；经确认是新空库输入 NEW_EMPTY:$databaseName"
    if ($backupConfirmation -notin @("BACKUP_VERIFIED:$databaseName", "NEW_EMPTY:$databaseName")) { throw '备份/空库确认不匹配；未运行迁移。' }
    $confirmation = Read-Host "再输入 MIGRATE:$databaseName 确认"
    if ($confirmation -ne "MIGRATE:$databaseName") { throw '未确认，未运行迁移。' }

    $migrationStamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
    $migrationLog = Join-Path $script:LogDir "database-migration.$migrationStamp.log"
    Push-Location $script:RepoRoot
    try {
        & $python -m alembic -c unified_system/alembic.ini upgrade head 2>&1 | Tee-Object -FilePath $migrationLog
        if ($LASTEXITCODE -ne 0) { throw "Alembic upgrade 失败；详见 $migrationLog" }
        & $python -m alembic -c unified_system/alembic.ini check 2>&1 | Tee-Object -FilePath $migrationLog -Append
        if ($LASTEXITCODE -ne 0) { throw "Alembic schema check 失败；详见 $migrationLog" }
        & $python -m flask --app unified_system.app:create_app seed-catalog 2>&1 | Tee-Object -FilePath $migrationLog -Append
        if ($LASTEXITCODE -ne 0) { throw "初始角色/权限/平台目录 seed 失败；详见 $migrationLog" }
        & $python (Join-Path $PSScriptRoot 'check_local_db.py')
        if ($LASTEXITCODE -ne 0) { throw "迁移后版本验证失败；详见 $migrationLog" }
    } finally {
        Pop-Location
    }
    Write-Host '数据库迁移和版本检查完成。数据库内容未被清空或重建。'
} catch {
    Write-Host ('数据库初始化/迁移失败：{0}' -f $_.Exception.Message) -ForegroundColor Red
    $scriptExitCode = 1
} finally {
    Restore-LocalSettingsEnvironment
}
if ($scriptExitCode -ne 0) { exit $scriptExitCode }
