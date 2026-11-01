$script:WindowsScriptsDir = $PSScriptRoot
$script:RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$script:UnifiedRoot = Join-Path $script:RepoRoot 'unified_system'
$script:RuntimeDir = Join-Path $script:UnifiedRoot 'data\windows-runtime'
$script:SettingsFile = Join-Path $script:RuntimeDir 'local.env'
$script:LogDir = Join-Path $script:RuntimeDir 'logs'

function Initialize-LocalRuntimeDirectories {
    New-Item -ItemType Directory -Force -Path $script:RuntimeDir, $script:LogDir | Out-Null
}

function Write-LocalLog {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ValidateSet('INFO', 'WARN', 'ERROR')][string]$Level = 'INFO'
    )
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Write-Host $line
    if (Test-Path $script:LogDir) {
        Add-Content -LiteralPath (Join-Path $script:LogDir 'launcher.log') -Value $line -Encoding UTF8
    }
}

$script:LocalEnvironmentOriginal = @{}

function Set-TrackedLocalEnvironment {
    param([Parameter(Mandatory = $true)][string]$Name, [AllowNull()][string]$Value)
    if (-not $script:LocalEnvironmentOriginal.ContainsKey($Name)) {
        $script:LocalEnvironmentOriginal[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
    }
    [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
}

function Restore-LocalSettingsEnvironment {
    foreach ($name in @($script:LocalEnvironmentOriginal.Keys)) {
        [Environment]::SetEnvironmentVariable($name, $script:LocalEnvironmentOriginal[$name], 'Process')
    }
    $script:LocalEnvironmentOriginal = @{}
}

function Import-LocalSettings {
    $allowedNames = @(
        'DATABASE_URL', 'APP_SECRET_KEY', 'CREDENTIAL_ENCRYPTION_KEYS_JSON',
        'CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID', 'POSTGRES_SERVICE_NAME',
        'LOCAL_DATABASE_NAME', 'LOCAL_DATABASE_PORT'
    )
    if (-not (Test-Path -LiteralPath $script:SettingsFile -PathType Leaf)) {
        throw "本地配置不存在：$($script:SettingsFile)。请先运行 scripts\windows\configure-local.ps1。"
    }
    foreach ($line in Get-Content -LiteralPath $script:SettingsFile) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith('#')) { continue }
        $separator = $line.IndexOf('=')
        if ($separator -lt 1) { continue }
        $name = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1)
        if ($name -notmatch '^[A-Z][A-Z0-9_]*$' -or $name -notin $allowedNames) { continue }
        Set-TrackedLocalEnvironment -Name $name -Value $value
    }
    if (-not $env:DATABASE_URL -or $env:DATABASE_URL -notmatch '^postgresql\+psycopg://') {
        throw '本地 DATABASE_URL 缺失或不是 PostgreSQL psycopg URL；拒绝使用 SQLite 启动。'
    }
    if ($env:DATABASE_URL -notmatch '^postgresql\+psycopg://[^/]*@(127\.0\.0\.1|localhost):\d+/') {
        throw 'DATABASE_URL 必须连接本机 127.0.0.1/localhost，拒绝远程数据库连接。'
    }
    if (-not $env:LOCAL_DATABASE_NAME -or $env:LOCAL_DATABASE_NAME -notmatch '^[A-Za-z_][A-Za-z0-9_]{0,62}$') {
        throw 'LOCAL_DATABASE_NAME 缺失或格式无效。'
    }
    if (-not $env:LOCAL_DATABASE_PORT -or $env:LOCAL_DATABASE_PORT -notmatch '^\d+$' -or [int]$env:LOCAL_DATABASE_PORT -lt 1 -or [int]$env:LOCAL_DATABASE_PORT -gt 65535) {
        throw 'LOCAL_DATABASE_PORT 缺失或格式无效。'
    }
    if (-not $env:POSTGRES_SERVICE_NAME -or -not $env:CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID -or -not $env:CREDENTIAL_ENCRYPTION_KEYS_JSON) {
        throw 'PostgreSQL 服务或凭据加密 keyring 配置不完整。'
    }
    $connectionUri = [Uri]($env:DATABASE_URL -replace '^postgresql\+psycopg://', 'postgresql://')
    $urlDatabaseName = [Uri]::UnescapeDataString($connectionUri.AbsolutePath.Trim('/'))
    if ($connectionUri.Port -ne [int]$env:LOCAL_DATABASE_PORT -or $urlDatabaseName -ne $env:LOCAL_DATABASE_NAME) {
        throw 'DATABASE_URL 的端口/数据库名与本地显式配置不一致；拒绝连接。'
    }
    if ([string]::IsNullOrWhiteSpace($env:APP_SECRET_KEY) -or $env:APP_SECRET_KEY.Length -lt 32) {
        throw 'APP_SECRET_KEY 不存在或长度不足；请重新生成本地配置。'
    }
    Set-TrackedLocalEnvironment -Name 'APP_ENV' -Value 'development'
    Set-TrackedLocalEnvironment -Name 'COOKIE_SECURE' -Value '0'
    Set-TrackedLocalEnvironment -Name 'TRUSTED_PROXY_HOPS' -Value '0'
    Set-TrackedLocalEnvironment -Name 'DATA_DIR' -Value (Join-Path $script:UnifiedRoot 'data')
    Set-TrackedLocalEnvironment -Name 'PYTHONUNBUFFERED' -Value '1'
    Set-TrackedLocalEnvironment -Name 'UNIFIED_LOCAL_RUNTIME_DIR' -Value $script:RuntimeDir
    Set-TrackedLocalEnvironment -Name 'UNIFIED_LOCAL_STOP_FILE' -Value (Join-Path $script:RuntimeDir 'stop.request')
    Set-TrackedLocalEnvironment -Name 'UNIFIED_LOCAL_PORT' -Value '8088'
}

function Get-PostgresServices {
    $all = @(Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '^postgresql' -or $_.DisplayName -match 'PostgreSQL'
    })
    return $all
}

function Get-PostgresVersionForService {
    param([Parameter(Mandatory = $true)]$Service)
    $binaryPath = [string]$Service.PathName
    if ($binaryPath -match '^\s*"([^"]+)"') { $serviceExe = $Matches[1] }
    elseif ($binaryPath -match '^\s*([^\s]+)') { $serviceExe = $Matches[1] }
    else { $serviceExe = $null }
    if ($serviceExe -and (Test-Path -LiteralPath $serviceExe)) {
        $versionLine = (& $serviceExe --version 2>$null | Select-Object -First 1)
        if ($versionLine -match '([0-9]+(?:\.[0-9]+)+)') { return $Matches[1] }
    }
    if ($Service.Name -match 'postgresql(?:-x64)?[-_ ]?(\d+)') { return $Matches[1] }
    if (Get-Command psql.exe -ErrorAction SilentlyContinue) {
        $versionLine = (& psql.exe --version 2>$null | Select-Object -First 1)
        if ($versionLine -match '([0-9]+(?:\.[0-9]+)+)') { return $Matches[1] }
    }
    return 'unknown'
}

function Get-PostgresBinDirectory {
    param([Parameter(Mandatory = $true)]$Service)
    $binaryPath = [string]$Service.PathName
    if ($binaryPath -match '^\s*"([^"]+)"') { $executable = $Matches[1] }
    elseif ($binaryPath -match '^\s*([^\s]+)') { $executable = $Matches[1] }
    else { return $null }
    $directory = Split-Path -Parent $executable
    if (Test-Path -LiteralPath (Join-Path $directory 'pg_dump.exe')) { return $directory }
    return $null
}

function Resolve-PostgresService {
    param([string]$ServiceName = $env:POSTGRES_SERVICE_NAME)
    $services = @(Get-PostgresServices)
    if ($ServiceName) {
        $match = $services | Where-Object { $_.Name -eq $ServiceName } | Select-Object -First 1
        if (-not $match) { throw "配置的 PostgreSQL 服务 '$ServiceName' 不存在。请运行 configure-local.ps1 重新选择。" }
        return $match
    }
    if ($services.Count -eq 0) {
        throw '未检测到 PostgreSQL Windows 服务。脚本不会自动安装 PostgreSQL。请按 docs/local-windows-runbook.md 安装并配置。'
    }
    if ($services.Count -gt 1) {
        $names = ($services | ForEach-Object { $_.Name }) -join ', '
        throw "检测到多个 PostgreSQL 服务（$names），请先运行 configure-local.ps1 选择服务。"
    }
    return $services[0]
}

function Start-PostgresService {
    param([string]$ServiceName = $env:POSTGRES_SERVICE_NAME)
    $serviceInfo = Resolve-PostgresService -ServiceName $ServiceName
    $service = Get-Service -Name $serviceInfo.Name -ErrorAction Stop
    if ($service.Status -eq 'Running') {
        Write-LocalLog "PostgreSQL 服务已在运行：$($service.Name)（版本 $((Get-PostgresVersionForService $serviceInfo))），不重复启动。"
        return $service
    }
    Write-LocalLog "正在启动 PostgreSQL 服务：$($service.Name)。不会触碰数据库目录。"
    Start-Service -Name $service.Name -ErrorAction Stop
    $service = Get-Service -Name $service.Name -ErrorAction Stop
    $service.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
    if ((Get-Service -Name $service.Name).Status -ne 'Running') {
        throw "PostgreSQL 服务未能进入 Running 状态：$($service.Name)"
    }
    Write-LocalLog "PostgreSQL 服务已启动：$($service.Name)（版本 $((Get-PostgresVersionForService $serviceInfo))）。"
    return $service
}

function Get-OwnedBackendProcess {
    $pidFile = Join-Path $script:RuntimeDir 'backend.pid'
    if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) { return $null }
    $rawPid = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    $pidValue = 0
    if (-not [int]::TryParse($rawPid, [ref]$pidValue) -or $pidValue -le 0) { return $null }
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $pidValue" -ErrorAction SilentlyContinue
    if (-not $processInfo) { return $null }
    $expectedPython = Join-Path $script:RepoRoot '.venv\Scripts\python.exe'
    if ($processInfo.ExecutablePath -and [System.IO.Path]::GetFullPath($processInfo.ExecutablePath) -ne [System.IO.Path]::GetFullPath($expectedPython)) { return $null }
    if ($processInfo.CommandLine -notmatch 'unified_system\.local_server') { return $null }
    return $processInfo
}

function Test-HttpEndpoint {
    param([Parameter(Mandatory = $true)][string]$Uri, [int]$TimeoutSec = 2)
    try {
        $response = Invoke-WebRequest -Uri $Uri -Method Get -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
        return [pscustomobject]@{ Ok = ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400); StatusCode = [int]$response.StatusCode }
    } catch {
        return [pscustomobject]@{ Ok = $false; StatusCode = 0 }
    }
}

function Get-ListeningPortInfo {
    param([int]$Port)
    try {
        return @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -Property LocalAddress, LocalPort, OwningProcess -Unique)
    } catch {
        return @()
    }
}
