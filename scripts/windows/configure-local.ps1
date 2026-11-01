$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Initialize-LocalRuntimeDirectories
$scriptExitCode = 0

try {
    if (Test-Path -LiteralPath $script:SettingsFile) {
        throw "配置文件已存在，为避免覆盖，未作更改：$($script:SettingsFile)。如需变更，请先自行备份该文件后再操作。"
    }

    $services = @(Get-PostgresServices)
    if ($services.Count -eq 0) {
        throw '没有检测到 PostgreSQL Windows 服务。请先手动安装 PostgreSQL；本向导不会安装系统软件。'
    }
    Write-Host '检测到本机 PostgreSQL 服务：'
    for ($i = 0; $i -lt $services.Count; $i++) {
        $state = (Get-Service -Name $services[$i].Name).Status
        $version = Get-PostgresVersionForService $services[$i]
        Write-Host ('  [{0}] {1} | 状态={2} | 版本={3}' -f ($i + 1), $services[$i].Name, $state, $version)
    }
    if ($services.Count -eq 1) {
        $selectedService = $services[0]
    } else {
        $selection = Read-Host '输入要用于本项目的服务编号（不会启动其他 PostgreSQL 服务）'
        $selectionNumber = 0
        if (-not [int]::TryParse($selection, [ref]$selectionNumber) -or $selectionNumber -lt 1 -or $selectionNumber -gt $services.Count) {
            throw '服务编号无效；未写入配置。'
        }
        $selectedService = $services[$selectionNumber - 1]
    }

    $portText = Read-Host 'PostgreSQL 本机端口 [5432]'
    if ([string]::IsNullOrWhiteSpace($portText)) { $portText = '5432' }
    $port = 0
    if (-not [int]::TryParse($portText, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw 'PostgreSQL 端口必须是 1–65535 的整数。'
    }
    $database = Read-Host '已存在的本地业务数据库名（默认 unified_local）'
    if ([string]::IsNullOrWhiteSpace($database)) { $database = 'unified_local' }
    if ($database -notmatch '^[A-Za-z_][A-Za-z0-9_]{0,62}$') {
        throw '数据库名仅允许以字母/下划线开头并包含字母、数字、下划线（最多 63 字符）。'
    }
    $dbUser = Read-Host '专用 PostgreSQL 登录角色（默认 unified_local_app；不要使用超级用户）'
    if ([string]::IsNullOrWhiteSpace($dbUser)) { $dbUser = 'unified_local_app' }
    if ($dbUser -notmatch '^[A-Za-z_][A-Za-z0-9_]{0,62}$') {
        throw '数据库用户名仅允许以字母/下划线开头并包含字母、数字、下划线（最多 63 字符）。'
    }
    $python = Join-Path $script:RepoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw '请先创建仓库 .venv 并安装 unified_system/requirements.txt，再配置本地数据库。' }
    $credentialKey = (& $python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode("ascii"))').Trim()
    if ($LASTEXITCODE -ne 0 -or -not $credentialKey) { throw '本地凭据加密密钥生成失败。' }
    $credentialKeyJson = @{ local = $credentialKey } | ConvertTo-Json -Compress

    $securePassword = Read-Host 'PostgreSQL 密码（不会回显）' -AsSecureString
    $passwordPtr = [IntPtr]::Zero
    try {
        $passwordPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
        $dbPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPtr)
    } finally {
        if ($passwordPtr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPtr) }
    }
    if ([string]::IsNullOrEmpty($dbPassword)) { throw 'PostgreSQL 密码不可为空。' }

    $encodedUser = [Uri]::EscapeDataString($dbUser)
    $encodedPassword = [Uri]::EscapeDataString($dbPassword)
    $databaseUrl = "postgresql+psycopg://${encodedUser}:${encodedPassword}@127.0.0.1:${port}/${database}"
    $randomBytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($randomBytes) } finally { $rng.Dispose() }
    $appSecret = [Convert]::ToBase64String($randomBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    $lines = @(
        "DATABASE_URL=$databaseUrl",
        "APP_SECRET_KEY=$appSecret",
        "CREDENTIAL_ENCRYPTION_KEYS_JSON=$credentialKeyJson",
        'CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID=local',
        "POSTGRES_SERVICE_NAME=$($selectedService.Name)",
        "LOCAL_DATABASE_NAME=$database",
        "LOCAL_DATABASE_PORT=$port"
    )
    try {
        Set-Content -LiteralPath $script:SettingsFile -Value $lines -Encoding ASCII
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        & icacls.exe $script:SettingsFile /inheritance:r /grant:r "*${sid}:(F)" /grant:r '*S-1-5-18:(F)' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'icacls 返回失败。' }
    } catch {
        Remove-Item -LiteralPath $script:SettingsFile -Force -ErrorAction SilentlyContinue
        throw '无法安全写入本地配置 ACL；不保留可能权限过宽的密钥文件，请检查文件夹权限后重试。'
    }

    $dbPassword = $null
    $securePassword = $null
    $encodedPassword = $null
    $databaseUrl = $null
    $credentialKey = $null
    $credentialKeyJson = $null
    $appSecret = $null
    [Array]::Clear($randomBytes, 0, $randomBytes.Length)
    $randomBytes = $null
    Write-Host ''
    Write-Host '本地配置已创建；密码和应用密钥没有打印。未创建、清空或迁移任何数据库。'
    Write-Host "配置文件：$($script:SettingsFile)（Git 忽略；ACL 限制到当前用户和 SYSTEM）"
    Write-Host "已选 PostgreSQL 服务：$($selectedService.Name)"
    Write-Host "数据库目标：127.0.0.1:$port/$database"
    Write-Host '下一步：如数据库尚不存在，请通过 pgAdmin/createdb 手动创建；然后先备份现有数据，再运行 initialize-local-db.ps1 执行前向 Alembic migration。'
} catch {
    Write-Host ('本地配置失败：{0}' -f $_.Exception.Message) -ForegroundColor Red
    $scriptExitCode = 1
} finally {
    if ($securePassword) { $securePassword.Dispose() }
    $dbPassword = $null
    $encodedPassword = $null
    $databaseUrl = $null
    $credentialKey = $null
    $credentialKeyJson = $null
    $appSecret = $null
    $lines = $null
    if ($randomBytes) { [Array]::Clear($randomBytes, 0, $randomBytes.Length) }
    $randomBytes = $null
}
if ($scriptExitCode -ne 0) { exit $scriptExitCode }
