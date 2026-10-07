param(
    [Parameter(Mandatory = $true)]
    [string]$ContainerName
)

$container = docker ps -a --filter "name=^$ContainerName$" --format '{{.Names}}'
if ($container -ne $ContainerName) {
    throw "container not found: $ContainerName"
}

$logs = docker logs $ContainerName 2>&1 | Out-String
$patterns = [ordered]@{
    service_token = 'p2m-service-token'
    signing_key = 'p2m-signing-key'
    database_password = 'p2m-root-password'
    input_hash = 'p2m-file-hash'
    csv_marker = 'A,B'
    sql_marker = 'SELECT'
    traceback = 'Traceback'
}
$hits = [ordered]@{}
foreach ($item in $patterns.GetEnumerator()) {
    $hits[$item.Key] = [regex]::Matches(
        $logs,
        [regex]::Escape($item.Value),
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    ).Count
}
[ordered]@{
    container = $ContainerName
    log_bytes = [System.Text.Encoding]::UTF8.GetByteCount($logs)
    hits = $hits
} | ConvertTo-Json -Compress
