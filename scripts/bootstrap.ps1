# 新设备初始化：把规律库 seed（seed/eventreplay.db）灌入运行库 data/eventreplay.db
# 用法：pwsh -File scripts/bootstrap.ps1 [-Force]
# 说明：seed 为静态快照；data/eventreplay.db 与 cache/ 不入 git，运行产物留在本地。
param([switch]$Force)
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$seed  = Join-Path $root 'seed\eventreplay.db'
$dbDir = Join-Path $root 'data'
$db    = Join-Path $dbDir 'eventreplay.db'

if (-not (Test-Path $seed)) {
    throw '缺少 seed\eventreplay.db：仓库不完整，请重新 clone 或检查是否被 .gitignore 漏掉'
}
$size = (Get-Item $seed).Length
if ($size -lt 1MB) {
    throw "seed\eventreplay.db 只有 $size 字节，疑似损坏或 LFS 指针文件；请重新获取仓库"
}

if ((Test-Path $db) -and -not $Force) {
    Write-Host ("data\eventreplay.db 已存在（{0:N0} 字节），跳过；如需覆盖请加 -Force" -f (Get-Item $db).Length)
} else {
    New-Item -ItemType Directory -Force -Path $dbDir | Out-Null
    Copy-Item $seed $db -Force
    Write-Host ("已灌入规律库 seed -> data\eventreplay.db（{0:N0} 字节）" -f $size)
}

if (-not (Test-Path (Join-Path $root '.env'))) {
    Write-Warning '未找到 .env：请复制 .env.example 为 .env 并填写 token；新设备务必设置 EMBEDDING_BACKEND=local（与库内 512 维向量一致）'
}

Write-Host '自检：eventreplay find-similar --desc "美联储加息" --top-k 3   # 期望返回 3 条 Pattern'
