[CmdletBinding()]
param(
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$projectName = "causalagent-searxng-validation-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
$artifactRoot = Join-Path ([IO.Path]::GetTempPath()) $projectName
$configRoot = Join-Path $artifactRoot "core-config"
$overridePath = Join-Path $artifactRoot "compose.validation.yml"
$validationSecret = "validation-searxng-secret-$([Guid]::NewGuid().ToString('N'))"
$composeArgs = @(
    "--project-name", $projectName,
    "-f", (Join-Path $repoRoot "docker-compose.yml"),
    "-f", $overridePath
)

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker CLI is unavailable; SearXNG Docker validation cannot run."
}

& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Engine is not running; SearXNG Docker validation cannot run."
}

New-Item -ItemType Directory -Path $configRoot -Force | Out-Null
# 开发与预发都提交非密钥 settings.yml；复制同一份文件作为隔离验证配置。
Copy-Item `
    -LiteralPath (Join-Path $repoRoot "searxng/core-config/settings.yml") `
    -Destination (Join-Path $configRoot "settings.yml")

$configRootForCompose = $configRoot.Replace("\", "/")
@"
services:
  searxng:
    container_name: ${projectName}_core
    volumes: !override
      - type: bind
        source: '$configRootForCompose'
        target: /etc/searxng
        read_only: true
      - searxng_validation_core_data:/var/cache/searxng

  valkey:
    container_name: ${projectName}_valkey

volumes:
  searxng_validation_core_data:
"@ | Set-Content -LiteralPath $overridePath -Encoding utf8

function Invoke-ValidationCompose {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & docker compose @composeArgs @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

$env:SEARXNG_SECRET = $validationSecret

try {
    Invoke-ValidationCompose -Arguments @("up", "-d", "--wait", "searxng")

    # 配置文件不含 secret_key，运行期取值必须等于环境变量注入值。
    $settingsText = Get-Content -Raw -LiteralPath (Join-Path $configRoot "settings.yml")
    $declaredSecretKey = $settingsText -split '\r?\n' |
        Where-Object { $_ -notmatch '^\s*#' -and $_ -match '^\s*secret_key\s*:' }
    if ($declaredSecretKey) {
        throw "settings.yml still declares secret_key; it must stay non-secret."
    }

    $probe = 'import searx; print("SECRET_OK" if searx.settings["server"]["secret_key"] == "' + $validationSecret + '" else "SECRET_MISMATCH")'
    $result = & docker compose @composeArgs exec -T searxng /usr/local/searxng/.venv/bin/python -c $probe
    if ($LASTEXITCODE -ne 0) {
        throw "SEARXNG_SECRET probe failed with exit code $LASTEXITCODE."
    }
    if (($result -join "`n") -notmatch "SECRET_OK") {
        throw "SEARXNG_SECRET was not applied; got: $($result -join ' ')"
    }

    Invoke-ValidationCompose -Arguments @(
        "exec",
        "-T",
        "searxng",
        "/usr/local/searxng/.venv/bin/python",
        "-c",
        "import urllib.request; response = urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2); assert response.status == 200; assert response.read() == b'OK'"
    )

    Write-Output "SearXNG Docker validation passed: SEARXNG_SECRET injection and /healthz."
}
finally {
    & docker compose @composeArgs down --volumes --remove-orphans *> $null
    Remove-Item Env:SEARXNG_SECRET -ErrorAction SilentlyContinue
    if (-not $KeepArtifacts -and (Test-Path -LiteralPath $artifactRoot)) {
        Remove-Item -LiteralPath $artifactRoot -Recurse -Force
    }
    elseif (Test-Path -LiteralPath $artifactRoot) {
        Write-Output "Validation artifacts kept at: $artifactRoot"
    }
}
