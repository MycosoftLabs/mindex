param(
    [ValidateSet('Start', 'Test', 'Stop')][string]$Action = 'Test',
    [string]$RuntimeDirectory = (Join-Path $PSScriptRoot '../../runtime'),
    [int]$Port = 55919
)
$ErrorActionPreference = 'Stop'
$taskRuntime = [IO.Path]::GetFullPath($RuntimeDirectory)
$taskPgBin = Join-Path $taskRuntime 'postgres/pgsql/bin'
$taskData = Join-Path $taskRuntime 'retention-fixture-pgdata'
$taskMarker = Join-Path $taskData '.retention-fixture-owner.json'
$taskPython = Join-Path $taskRuntime 'Scripts/python.exe'
$taskDatabase = 'retention_fixture_brief09_rehearsal'
$taskRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Use an unprivileged loopback fixture port.' }
foreach ($taskExecutable in @('initdb.exe', 'pg_ctl.exe', 'createdb.exe', 'psql.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $taskPgBin $taskExecutable))) {
        throw "Missing portable PostgreSQL executable: $taskExecutable"
    }
}
if (Test-Path -LiteralPath $taskData) {
    if (-not (Test-Path -LiteralPath $taskMarker)) { throw 'Refusing an unmarked database directory.' }
    $taskOwner = Get-Content -LiteralPath $taskMarker -Raw | ConvertFrom-Json
    if ($taskOwner.purpose -ne 'brief09-disposable-retention' -or $taskOwner.port -ne $Port -or
        $taskOwner.directory -ne $taskData) { throw 'Fixture ownership marker does not match.' }
} elseif ($Action -eq 'Stop') {
    throw 'No owned fixture exists at this path.'
} else {
    & (Join-Path $taskPgBin 'initdb.exe') -D $taskData -U retention_fixture --auth=trust --encoding=UTF8 --locale=C
    if ($LASTEXITCODE -ne 0) { throw 'Fixture initdb failed.' }
    @{purpose = 'brief09-disposable-retention'; directory = $taskData; port = $Port} |
        ConvertTo-Json | Set-Content -LiteralPath $taskMarker -Encoding utf8
}
if ($Action -eq 'Stop') {
    & (Join-Path $taskPgBin 'pg_ctl.exe') -D $taskData -m fast -w stop
    if ($LASTEXITCODE -ne 0) { throw 'Fixture stop failed.' }
    return
}
& (Join-Path $taskPgBin 'pg_ctl.exe') -D $taskData status *> $null
$taskStartedHere = $LASTEXITCODE -ne 0
if ($taskStartedHere) {
    & (Join-Path $taskPgBin 'pg_ctl.exe') -D $taskData -l (Join-Path $taskRuntime 'retention-fixture-postgres.log') `
        -o "-h 127.0.0.1 -p $Port" -w start
    if ($LASTEXITCODE -ne 0) { throw 'Fixture PostgreSQL start failed.' }
}
$taskOldDsn = $env:RETENTION_TEST_DSN
$taskOldAllow = $env:RETENTION_TEST_ALLOW_DISPOSABLE
$taskLeaveRunning = $false
try {
    $taskExists = & (Join-Path $taskPgBin 'psql.exe') -h 127.0.0.1 -p $Port -U retention_fixture -d postgres `
        -At -c "SELECT 1 FROM pg_database WHERE datname='retention_fixture_brief09_rehearsal'"
    if ($LASTEXITCODE -ne 0) { throw 'Fixture database lookup failed.' }
    if ($taskExists -ne '1') {
        & (Join-Path $taskPgBin 'createdb.exe') -h 127.0.0.1 -p $Port -U retention_fixture $taskDatabase
        if ($LASTEXITCODE -ne 0) { throw 'Fixture createdb failed.' }
    }
    if ($Action -eq 'Start') {
        Write-Output "Owned disposable PostgreSQL running on 127.0.0.1:$Port; database=$taskDatabase"
        Write-Output "Stop with this script -Action Stop -RuntimeDirectory '$taskRuntime' -Port $Port"
        $taskLeaveRunning = $true
        return
    }
    if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Missing task virtual environment Python.' }
    $env:RETENTION_TEST_DSN = "postgresql://retention_fixture@127.0.0.1:$Port/$taskDatabase"
    $env:RETENTION_TEST_ALLOW_DISPOSABLE = '1'
    Push-Location $taskRepo
    try {
        & $taskPython -m pytest tests/test_retention_postgres.py -q --tb=short
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL boundary tests failed.' }
    } finally { Pop-Location }
} finally {
    $env:RETENTION_TEST_DSN = $taskOldDsn
    $env:RETENTION_TEST_ALLOW_DISPOSABLE = $taskOldAllow
    if ($taskStartedHere -and -not $taskLeaveRunning) {
        & (Join-Path $taskPgBin 'pg_ctl.exe') -D $taskData -m fast -w stop
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Could not stop the owned fixture PostgreSQL; inspect its data directory.' }
    }
}
