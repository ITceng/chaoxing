param([string]$ServiceName = '')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Initialize-LocalRuntimeDirectories

try {
    if (-not $ServiceName -and (Test-Path -LiteralPath $script:SettingsFile -PathType Leaf)) {
        $serviceLine = Get-Content -LiteralPath $script:SettingsFile | Where-Object { $_ -match '^POSTGRES_SERVICE_NAME=' } | Select-Object -First 1
        if ($serviceLine) { $ServiceName = $serviceLine.Substring('POSTGRES_SERVICE_NAME='.Length) }
    }
    $null = Start-PostgresService -ServiceName $ServiceName
} catch {
    Write-LocalLog -Level ERROR -Message $_.Exception.Message
    exit 1
}
