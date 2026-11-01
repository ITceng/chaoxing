$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'common.ps1')
Write-Host '=== Windows 本机运行环境只读检查 ==='
Write-Host "项目根目录：$($script:RepoRoot)"
$rootPathLength = $script:RepoRoot.Length
$longPaths = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -ErrorAction SilentlyContinue
$longPathsValue = if ($null -ne $longPaths) { $longPaths.LongPathsEnabled } else { '未配置/未知' }
Write-Host "Windows 路径：字符数=$rootPathLength；LongPathsEnabled=$longPathsValue；过深依赖路径可能失败"
Write-Host "PowerShell：$($PSVersionTable.PSVersion)"
$os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
if ($os) { Write-Host "Windows：$($os.Caption) build=$($os.BuildNumber)" }

$git = Get-Command git.exe -ErrorAction SilentlyContinue
if ($git) {
    Write-Host "Git：$(& git.exe --version 2>&1 | Select-Object -First 1)"
    $branch = (& git.exe -C $script:RepoRoot branch --show-current 2>$null | Select-Object -First 1)
    $commit = (& git.exe -C $script:RepoRoot rev-parse --short=12 HEAD 2>$null | Select-Object -First 1)
    $dirty = @(& git.exe -C $script:RepoRoot status --porcelain=v1 2>$null).Count
    Write-Host "Git 仓库：branch=$branch commit=$commit changed_or_untracked=$dirty"
} else { Write-Host 'Git：未安装/未加入 PATH' }

$python = Join-Path $script:RepoRoot '.venv\Scripts\python.exe'
$launcher = Get-Command py.exe -ErrorAction SilentlyContinue
$globalPython = Get-Command python.exe -ErrorAction SilentlyContinue
if ($launcher) { $pythonPaths = (& py.exe -0p 2>&1 | Out-String).Trim(); Write-Host "Python launcher：$pythonPaths" }
elseif ($globalPython) { Write-Host "系统 Python：$(& python.exe --version 2>&1 | Select-Object -First 1)" }
else { Write-Host 'Python：未检测到 py.exe/python.exe' }
if (Test-Path -LiteralPath $python) {
    Write-Host "项目 .venv：存在；$(& $python --version 2>&1 | Select-Object -First 1)"
    & $python -c 'import flask, sqlalchemy, psycopg, alembic, argon2, cryptography, werkzeug'
    if ($LASTEXITCODE -eq 0) { Write-Host '后端依赖导入：PASS' } else { Write-Host '后端依赖导入：FAIL（需按 runbook 安装一次）' }
} else { Write-Host '项目 .venv：缺失' }

if (Get-Command node.exe -ErrorAction SilentlyContinue) { Write-Host "Node.js：$(& node.exe --version 2>&1 | Select-Object -First 1)" } else { Write-Host 'Node.js：未安装/未加入 PATH' }
if (Get-Command npm.cmd -ErrorAction SilentlyContinue) { Write-Host "npm：$(& npm.cmd --version 2>&1 | Select-Object -First 1)" } else { Write-Host 'npm：未安装/未加入 PATH' }
$frontend = Join-Path $script:UnifiedRoot 'frontend'
Write-Host "前端目录：$(Test-Path -LiteralPath $frontend)"
Write-Host "前端 package.json：$(Test-Path -LiteralPath (Join-Path $frontend 'package.json'))"
Write-Host "前端依赖入口：$(Test-Path -LiteralPath (Join-Path $frontend 'node_modules\vite\bin\vite.js'))"
Write-Host "前端构建产物：$(Test-Path -LiteralPath (Join-Path $frontend 'dist\index.html'))"

$services = @(Get-PostgresServices)
if ($services.Count -eq 0) { Write-Host 'PostgreSQL Windows 服务：未检测到（不会安装软件）' }
else {
    foreach ($service in $services) {
        $state = (Get-Service -Name $service.Name -ErrorAction SilentlyContinue).Status
        $version = Get-PostgresVersionForService $service
        Write-Host "PostgreSQL 服务：$($service.Name) 状态=$state 版本=$version"
    }
}
if (Get-Command psql.exe -ErrorAction SilentlyContinue) { Write-Host "psql：$(& psql.exe --version 2>&1 | Select-Object -First 1)" } else { Write-Host 'psql：未加入 PATH（服务可能仍已安装）' }

Write-Host "Alembic 配置：$(Test-Path -LiteralPath (Join-Path $script:UnifiedRoot 'alembic.ini'))"
Write-Host "迁移目录：$(Test-Path -LiteralPath (Join-Path $script:UnifiedRoot 'migrations\versions'))"
Write-Host "本地配置文件：$(Test-Path -LiteralPath $script:SettingsFile)（只报告是否存在，不读取/打印内容）"
foreach ($port in @(5432, 8088, 5173)) {
    $listeners = @(Get-ListeningPortInfo -Port $port)
    if ($listeners.Count -eq 0) { Write-Host "端口 $port：未监听" }
    else {
        $owners = ($listeners | ForEach-Object { "pid=$($_.OwningProcess) address=$($_.LocalAddress)" }) -join ', '
        Write-Host "端口 $port：LISTEN $owners"
    }
}
Write-Host '此预检不启动/停止服务、不连接数据库、不安装软件、不修改文件/权限、不运行迁移。'
