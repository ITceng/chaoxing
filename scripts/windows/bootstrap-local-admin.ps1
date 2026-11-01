$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$python = Join-Path $script:RepoRoot '.venv\Scripts\python.exe'
$previousBootstrapEmail = [Environment]::GetEnvironmentVariable('BOOTSTRAP_ADMIN_EMAIL', 'Process')
$previousBootstrapPassword = [Environment]::GetEnvironmentVariable('BOOTSTRAP_ADMIN_PASSWORD', 'Process')
$scriptExitCode = 0

try {
    Import-LocalSettings
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw '找不到仓库 .venv Python；请先按 runbook 安装后端依赖。' }
    $env:BOOTSTRAP_ADMIN_EMAIL = (Read-Host '管理员邮箱').Trim()
    $secure = Read-Host '管理员密码（至少 12 位；不会回显）' -AsSecureString
    $passwordPtr = [IntPtr]::Zero
    try {
        $passwordPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $env:BOOTSTRAP_ADMIN_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPtr)
    } finally {
        if ($passwordPtr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPtr) }
    }
    if ($env:BOOTSTRAP_ADMIN_PASSWORD.Length -lt 12) { throw '管理员密码至少需要 12 位。' }
    Push-Location $script:RepoRoot
    try {
        & $python -m flask --app unified_system.app:create_app bootstrap-admin
        if ($LASTEXITCODE -ne 0) { throw '管理员创建失败；请看命令输出。' }
    } finally { Pop-Location }
} catch {
    Write-Host ('管理员初始化失败：{0}' -f $_.Exception.Message) -ForegroundColor Red
    $scriptExitCode = 1
} finally {
    Restore-LocalSettingsEnvironment
    [Environment]::SetEnvironmentVariable('BOOTSTRAP_ADMIN_EMAIL', $previousBootstrapEmail, 'Process')
    [Environment]::SetEnvironmentVariable('BOOTSTRAP_ADMIN_PASSWORD', $previousBootstrapPassword, 'Process')
    if ($secure) { $secure.Dispose() }
    $secure = $null
    $previousBootstrapEmail = $null
    $previousBootstrapPassword = $null
}
if ($scriptExitCode -ne 0) { exit $scriptExitCode }
