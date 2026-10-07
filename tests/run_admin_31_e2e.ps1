param(
    [switch]$ReuseExisting,
    [switch]$KeepSeededData
)

$ErrorActionPreference = "Stop"

function New-Admin31Secret {
    param([int]$Bytes = 24)
    $buffer = New-Object byte[] $Bytes
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($buffer)
    } finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($buffer).Replace("+", "A").Replace("/", "B").TrimEnd("=")
}

function Assert-Admin31Exit {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

$primaryContainer = "causalagent31e2e_mysql_primary"
$replicaContainer = "causalagent31e2e_mysql_replica"
$postgresContainer = "causalagent31e2e_postgres_checkpoint"
$existingContainers = docker ps -a --format "{{.Names}}"
Assert-Admin31Exit "docker ps"
$primaryExists = $existingContainers -contains $primaryContainer
$replicaExists = $existingContainers -contains $replicaContainer
$postgresExists = $existingContainers -contains $postgresContainer
if (($primaryExists -or $replicaExists -or $postgresExists) -and -not $ReuseExisting) {
    throw "Existing 3.1/3.2 E2E containers found; refusing to overwrite or delete them."
}
if ($ReuseExisting -and -not ($primaryExists -and $replicaExists -and $postgresExists)) {
    throw "ReuseExisting requires all isolated 3.1/3.2 database containers."
}
if ($KeepSeededData) {
    throw "3.2 includes physical-delete E2E and cannot reuse already-mutated seed data."
}

$env:MYSQL_ROOT_PASSWORD = New-Admin31Secret
$env:MYSQL_DATABASE = "causalagent31e2e"
$env:MYSQL_USER = "compat31"
$env:MYSQL_PASSWORD = New-Admin31Secret
$env:MYSQL_WRITE_USER = "writer31"
$env:MYSQL_WRITE_PASSWORD = New-Admin31Secret
$env:MYSQL_READ_USER = "reader31"
$env:MYSQL_READ_PASSWORD = New-Admin31Secret
$env:MYSQL_REPLICA_STATUS_USER = "status31"
$env:MYSQL_REPLICA_STATUS_PASSWORD = New-Admin31Secret
$env:MYSQL_REPLICATION_USER = "replica31"
$env:MYSQL_REPLICATION_PASSWORD = New-Admin31Secret
$env:CHECKPOINT_POSTGRES_DATABASE = "causalagent31e2e_checkpoints"
$env:CHECKPOINT_POSTGRES_USER = "checkpoint31"
$env:CHECKPOINT_POSTGRES_PASSWORD = New-Admin31Secret
$env:API_KEY = "isolated-e2e-not-used"
$env:BASE_URL = "https://example.invalid"
$env:MODEL = "isolated-e2e-model"
$env:SECRET_KEY = New-Admin31Secret 48
$env:E2E_ADMIN_PASSWORD = New-Admin31Secret
$env:E2E_USER_PASSWORD = New-Admin31Secret

$env:MYSQL_HOST = "127.0.0.1"
$env:MYSQL_WRITE_HOST = "127.0.0.1"
$env:MYSQL_READ_HOSTS = "127.0.0.2"
$env:MYSQL_PORT = "13317"
$env:CHECKPOINT_POSTGRES_HOST = "127.0.0.1"
$env:CHECKPOINT_POSTGRES_PORT = "15433"
$env:MYSQL_POOL_SIZE_WRITE = "5"
$env:MYSQL_POOL_SIZE_READ = "5"
$env:MYSQL_CONNECT_TIMEOUT_SECONDS = "5"
$env:MYSQL_POOL_ACQUIRE_TIMEOUT_SECONDS = "3"
$env:MYSQL_POOL_ACQUIRE_RETRY_MS = "50"
$env:MYSQL_REPLICA_MAX_LAG_SECONDS = "5"
$env:MYSQL_REPLICA_STATUS_CACHE_SECONDS = "2"
$env:ADMIN_BATCH_MAX_TARGETS = "20"
$env:ADMIN_DELETE_MAX_RELATED_ROWS = "10000"
$env:ADMIN_DB_LOCK_WAIT_TIMEOUT_SECONDS = "5"
$env:DB_INSPECTION_QUERY_TIMEOUT_MS = "3000"
$env:ADMIN_FRONTEND_DIST_DIR = (Resolve-Path "admin-frontend/dist").Path
$env:ADMIN_VITE_DEV_SERVER_URL = ""
$env:FLASK_ENV = "development"
$env:PLAYWRIGHT_BASE_URL = "http://127.0.0.1:15011"
$env:PLAYWRIGHT_ADMIN_USERNAME = "e2e-admin-31"
$env:PLAYWRIGHT_ADMIN_PASSWORD = $env:E2E_ADMIN_PASSWORD
$env:PLAYWRIGHT_USER_USERNAME = "e2e-user-31"
$env:PLAYWRIGHT_USER_PASSWORD = $env:E2E_USER_PASSWORD

$flaskProcess = $null
$monitorProcess = $null
try {
    if ($ReuseExisting) {
        $rotationSql = @"
ALTER USER 'compat31'@'%' IDENTIFIED BY '$($env:MYSQL_PASSWORD)';
ALTER USER 'writer31'@'%' IDENTIFIED BY '$($env:MYSQL_WRITE_PASSWORD)';
ALTER USER 'reader31'@'%' IDENTIFIED BY '$($env:MYSQL_READ_PASSWORD)';
ALTER USER 'status31'@'%' IDENTIFIED BY '$($env:MYSQL_REPLICA_STATUS_PASSWORD)';
FLUSH PRIVILEGES;
"@
        $rotationSql | docker exec -i $primaryContainer sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD"'
        Assert-Admin31Exit "rotate isolated credentials"
        $postgresRotationSql = "ALTER USER checkpoint31 WITH PASSWORD '$($env:CHECKPOINT_POSTGRES_PASSWORD)';"
        $postgresRotationSql | docker exec -i $postgresContainer `
            psql -v ON_ERROR_STOP=1 -U checkpoint31 -d causalagent31e2e_checkpoints
        Assert-Admin31Exit "rotate isolated PostgreSQL credential"
    } else {
        docker-compose `
            -p causalagent31e2e `
            -f docker-compose.yml `
            -f docker-compose.admin-e2e.yml `
            up -d --build mysql-primary mysql-replica postgres-checkpoint
        Assert-Admin31Exit "start isolated MySQL pair and PostgreSQL checkpoint"
    }

    $healthDeadline = [DateTime]::UtcNow.AddMinutes(3)
    do {
        $primaryHealth = docker inspect --format "{{.State.Health.Status}}" $primaryContainer
        $replicaHealth = docker inspect --format "{{.State.Health.Status}}" $replicaContainer
        $postgresHealth = docker inspect --format "{{.State.Health.Status}}" $postgresContainer
        if (
            $primaryHealth -eq "healthy" -and
            $replicaHealth -eq "healthy" -and
            $postgresHealth -eq "healthy"
        ) {
            break
        }
        if ([DateTime]::UtcNow -ge $healthDeadline) {
            throw "Timed out waiting for the isolated 3.1/3.2 databases."
        }
        Start-Sleep -Seconds 2
    } while ($true)

    if ($ReuseExisting) {
        python -c "from app.db import get_write_connection; c=get_write_connection(); c.close()"
        Assert-Admin31Exit "database connectivity"
    } else {
        python Database/database_init.py
        Assert-Admin31Exit "database readiness"
    }
    alembic upgrade head
    Assert-Admin31Exit "empty database upgrade"
    alembic downgrade d3e4f5a6b7c8
    Assert-Admin31Exit "3.2 migration downgrade"
    python Database/audit_before_db_upgrade.py
    Assert-Admin31Exit "3.2 existing structure preflight"
    alembic upgrade head
    Assert-Admin31Exit "3.2 existing structure upgrade"
    docker-compose `
        -p causalagent31e2e `
        -f docker-compose.yml `
        -f docker-compose.admin-e2e.yml `
        up -d --build agent-persistence-cleanup
    Assert-Admin31Exit "start isolated Agent persistence cleanup"
    python -m tests.e2e.admin.seed_admin_31_e2e
    Assert-Admin31Exit "seed isolated 3.1/3.2 fixtures"

    $monitorProcess = Start-Process `
        -FilePath "python" `
        -ArgumentList "-m", "Database.monitor_worker" `
        -PassThru `
        -WindowStyle Hidden
    $flaskProcess = Start-Process `
        -FilePath "python" `
        -ArgumentList "-m", "flask", "--app", "CausalAgent", "run", "--host", "127.0.0.1", "--port", "15011", "--no-reload" `
        -PassThru `
        -WindowStyle Hidden

    $webDeadline = [DateTime]::UtcNow.AddMinutes(2)
    do {
        try {
            $response = Invoke-WebRequest -Uri "$($env:PLAYWRIGHT_BASE_URL)/" -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                break
            }
        } catch {
            if ($flaskProcess.HasExited) {
                throw "The isolated 3.1/3.2 Flask process exited early."
            }
        }
        if ([DateTime]::UtcNow -ge $webDeadline) {
            throw "Timed out waiting for the isolated 3.1/3.2 Flask server."
        }
        Start-Sleep -Seconds 1
    } while ($true)

    Push-Location admin-frontend
    try {
        npm run test:e2e
        Assert-Admin31Exit "real Playwright E2E"
    } finally {
        Pop-Location
    }
    python -m tests.e2e.admin.verify_admin_31_e2e
    Assert-Admin31Exit "database verification"
} finally {
    if ($flaskProcess -and -not $flaskProcess.HasExited) {
        Stop-Process -Id $flaskProcess.Id
    }
    if ($monitorProcess -and -not $monitorProcess.HasExited) {
        Stop-Process -Id $monitorProcess.Id
    }
    Write-Host "Isolated database containers and volumes were retained: $primaryContainer, $replicaContainer, $postgresContainer"
}
