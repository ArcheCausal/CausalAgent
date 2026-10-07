<#
.SYNOPSIS
    按需启动 CausalAgent 四个 Vue 前端工程的 Vite 开发服务器。

.DESCRIPTION
    脚本只使用 PowerShell 内置命令和本机 npm，不引入任何 npm 依赖。
    开发服务器的端口与资源前缀从各工程自己的 vite.config.ts 读取，避免与前端配置不一致。
    端口已被占用的前端会跳过，缺少 node_modules 的前端会提示先安装依赖。

.PARAMETER Frontends
    需要启动的前端，可用值为 website、chat、admin、rag，默认四个全部启动。

.PARAMETER Install
    启动前先在选中的前端目录执行 npm ci。

.EXAMPLE
    .\scripts\dev_frontends.ps1

.EXAMPLE
    .\scripts\dev_frontends.ps1 -Frontends website,chat

.EXAMPLE
    .\scripts\dev_frontends.ps1 -Frontends rag -Install

.EXAMPLE
    .\scripts\dev_frontends.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string[]]$Frontends,
    [switch]$Install
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot

# 前端短名称到工程目录的映射，目录相对于仓库根目录。
$frontendDirectories = [ordered]@{
    website = "website-frontend"
    chat    = "chat-frontend"
    admin   = "admin-frontend"
    rag     = "app/rag_eval/frontend"
}

# 允许用工程目录名或常见写法代替短名称。
$frontendAliases = @{
    "website-frontend"  = "website"
    "chat-frontend"     = "chat"
    "admin-frontend"    = "admin"
    "rag-eval"          = "rag"
    "rag_eval"          = "rag"
    "rag-eval-frontend" = "rag"
}

function Get-ViteConfigValue {
    param(
        [string]$ConfigText,
        [string]$Pattern,
        [string]$ConfigPath
    )

    $match = [regex]::Match($ConfigText, $Pattern)
    if (-not $match.Success) {
        throw "无法从 $ConfigPath 读取开发服务器配置，请确认 vite.config.ts 仍包含对应字段。"
    }

    return $match.Groups[1].Value
}

function Test-PortListening {
    param([int]$Port)

    # 分别探测 IPv4 与 IPv6：管理员、普通端和官网的 vite 用 --host 0.0.0.0，只监听 IPv4；
    # RAG 评测台的 vite 没有指定 host，只监听 IPv6 的 ::1。
    foreach ($address in "127.0.0.1", "::1") {
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $connect = $client.BeginConnect($address, $Port, $null, $null)
            if ($connect.AsyncWaitHandle.WaitOne(300)) {
                $client.EndConnect($connect)
                return $true
            }
        }
        catch {
            # 该地址上没有服务，继续检查下一个地址。
        }
        finally {
            $client.Close()
        }
    }

    return $false
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "未找到 npm，请先安装 Node.js 并把 npm 加入 PATH。"
}

$requested = @()
if ($Frontends -and $Frontends.Count -gt 0) {
    foreach ($value in $Frontends) {
        foreach ($token in ($value -split ",")) {
            $name = $token.Trim().ToLowerInvariant()
            if ($name -eq "") {
                continue
            }
            if ($name -eq "all") {
                $requested += $frontendDirectories.Keys
                continue
            }
            if ($frontendAliases.ContainsKey($name)) {
                $name = $frontendAliases[$name]
            }
            if (-not $frontendDirectories.Contains($name)) {
                throw "未知的前端名称 '$token'，可用值为 website、chat、admin、rag，或用 all 表示全部。"
            }
            $requested += $name
        }
    }
}
else {
    $requested += $frontendDirectories.Keys
}

$requested = @($requested | Select-Object -Unique)
if ($requested.Count -eq 0) {
    throw "没有需要启动的前端。"
}

$started = @()
$skipped = @()

foreach ($name in $requested) {
    $relativeDirectory = $frontendDirectories[$name]
    $directory = Join-Path $repoRoot $relativeDirectory
    $configPath = Join-Path $directory "vite.config.ts"

    if (-not (Test-Path -LiteralPath $configPath)) {
        throw "找不到 $relativeDirectory/vite.config.ts，无法确认开发服务器端口。"
    }

    $configText = Get-Content -LiteralPath $configPath -Raw
    $port = [int](Get-ViteConfigValue -ConfigText $configText -Pattern 'port:\s*(\d+)' -ConfigPath $configPath)
    $base = Get-ViteConfigValue -ConfigText $configText -Pattern 'base:\s*[''"]([^''"]+)[''"]' -ConfigPath $configPath
    $url = "http://localhost:$port$base"

    if (Test-PortListening -Port $port) {
        Write-Host "[$name] 端口 $port 已有服务在监听，跳过。"
        $skipped += "$name（端口 $port 已被占用）"
        continue
    }

    if ($Install -and $PSCmdlet.ShouldProcess($relativeDirectory, "npm ci")) {
        Write-Host "[$name] 在 $relativeDirectory 执行 npm ci"
        Push-Location -LiteralPath $directory
        try {
            & npm ci
            if ($LASTEXITCODE -ne 0) {
                throw "npm ci 在 $relativeDirectory 失败，退出码 $LASTEXITCODE。"
            }
        }
        finally {
            Pop-Location
        }
    }

    if (-not (Test-Path -LiteralPath (Join-Path $directory "node_modules"))) {
        Write-Warning "[$name] $relativeDirectory 缺少 node_modules，跳过；请先执行 npm ci，或使用 -Install 让脚本安装依赖。"
        $skipped += "$name（缺少 node_modules）"
        continue
    }

    if (-not $PSCmdlet.ShouldProcess("$relativeDirectory（端口 $port）", "启动 npm run dev")) {
        continue
    }

    $process = Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "title $name $port && npm run dev" -WorkingDirectory $directory -PassThru
    Write-Host "[$name] 已启动 Vite：$url（PID $($process.Id)）"
    $started += [pscustomobject]@{
        Name      = $name
        Directory = $relativeDirectory
        Port      = $port
        Url       = $url
        ProcessId = $process.Id
    }
}

Write-Host ""
if ($started.Count -gt 0) {
    Write-Host "已启动的前端开发服务器："
    foreach ($item in $started) {
        Write-Host ("  {0,-8} {1}  (PID {2})" -f $item.Name, $item.Url, $item.ProcessId)
    }
    Write-Host "在各自窗口按 Ctrl+C 可以停止对应的 Vite。"
}
if ($skipped.Count -gt 0) {
    Write-Host "跳过的前端："
    foreach ($item in $skipped) {
        Write-Host "  $item"
    }
}
