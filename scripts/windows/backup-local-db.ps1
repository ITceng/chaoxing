$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Initialize-LocalRuntimeDirectories
$previousPgPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'Process')
$scriptExitCode = 0

try {
    Import-LocalSettings
    $service = Start-PostgresService -ServiceName $env:POSTGRES_SERVICE_NAME
    $python = Join-Path $script:RepoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw '找不到仓库 .venv Python，不能验证数据库连接。' }
    & $python (Join-Path $PSScriptRoot 'check_local_db.py') --connect-only
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL 连接检查失败；没有开始备份。' }
    $binDir = Get-PostgresBinDirectory -Service $service
    if (-not $binDir) { throw '无法从 PostgreSQL Windows 服务路径定位 pg_dump.exe；请把同版本 PostgreSQL bin 目录加入 PATH。' }
    $pgDump = Join-Path $binDir 'pg_dump.exe'
    $pgRestore = Join-Path $binDir 'pg_restore.exe'
    if (-not (Test-Path -LiteralPath $pgDump) -or -not (Test-Path -LiteralPath $pgRestore)) { throw '未找到 pg_dump.exe/pg_restore.exe。' }

    if ($env:DATABASE_URL -notmatch '^postgresql\+psycopg://(?<dbUser>[A-Za-z_][A-Za-z0-9_]{0,62}):(?<dbPassword>[^@]*)@(?<hostName>127\.0\.0\.1|localhost):(?<dbPort>\d+)/(?<dbName>[A-Za-z_][A-Za-z0-9_]{0,62})$') {
        throw 'DATABASE_URL 格式无效或不是本机数据库；请运行 configure-local.ps1。'
    }
    $dbUser = [Uri]::UnescapeDataString($Matches.dbUser)
    $dbPassword = [Uri]::UnescapeDataString($Matches.dbPassword)
    $hostName = $Matches.hostName
    $port = [int]$Matches.dbPort
    $dbName = $Matches.dbName

    $backupDir = Join-Path $script:UnifiedRoot 'data\backups'
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $backupDir /inheritance:r /grant:r "*${sid}:(OI)(CI)(F)" /grant:r '*S-1-5-18:(OI)(CI)(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '无法限制备份目录 ACL；拒绝写入备份。' }
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
    $archive = Join-Path $backupDir "${dbName}_${stamp}.dump"

    $env:PGPASSWORD = $dbPassword
    try {
        & $pgDump --host $hostName --port $port --username $dbUser --dbname $dbName --format=custom --no-password --file $archive
        $dumpExit = $LASTEXITCODE
    } finally { [Environment]::SetEnvironmentVariable('PGPASSWORD', $previousPgPassword, 'Process'); $dbPassword = $null }
    if ($dumpExit -ne 0 -or -not (Test-Path -LiteralPath $archive -PathType Leaf) -or (Get-Item $archive).Length -eq 0) {
        Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
        throw "pg_dump 失败（exit=$dumpExit）；没有可用备份。"
    }
    & icacls.exe $archive /inheritance:r /grant:r "*${sid}:(F)" /grant:r '*S-1-5-18:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '备份文件 ACL 设置失败；不要共享此文件，请检查其权限。' }
    & $pgRestore --list $archive *> $null
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
        throw 'pg_restore 无法读取新备份；已删除本次创建的不完整文件。'
    }
    $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifest = "$archive.sha256"
    Set-Content -LiteralPath $manifest -Value "$hash  $([IO.Path]::GetFileName($archive))" -Encoding ASCII
    & icacls.exe $manifest /inheritance:r /grant:r "*${sid}:(F)" /grant:r '*S-1-5-18:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '备份完成，但校验文件 ACL 设置失败；请保护备份目录并检查权限。' }
    Write-Host "PostgreSQL custom-format 备份成功并通过 TOC 检查：$archive"
    Write-Host "SHA-256：$hash"
    Write-Host '备份未加密；请保存在受 BitLocker/受限 Windows 账户保护的位置，不要提交到 Git 或发送到聊天。'
} catch {
    Write-Host ('本地数据库备份失败：{0}' -f $_.Exception.Message) -ForegroundColor Red
    $scriptExitCode = 1
} finally {
    Restore-LocalSettingsEnvironment
    $dbPassword = $null
    $previousPgPassword = $null
}
if ($scriptExitCode -ne 0) { exit $scriptExitCode }
