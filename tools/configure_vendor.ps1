$ErrorActionPreference = 'Stop'
$projectDir = Split-Path $PSScriptRoot -Parent
$account = (Read-Host '厂家专用查询账号').Trim()
$secret = Read-Host '厂家密码（输入不显示）' -AsSecureString
$projectId = (Read-Host '项目 ID pid（不是设备编号）').Trim()
if (!$account -or $secret.Length -eq 0 -or $projectId -notmatch '^\d+$') { throw '账号、密码或项目 ID 未填写正确' }
$configPath = Join-Path $projectDir 'config/transmitter.json'
$backupPath = Join-Path $projectDir 'config/transmitter.before-vendor.local.json'
if (!(Test-Path -LiteralPath $backupPath)) { Copy-Item -LiteralPath $configPath -Destination $backupPath }
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
$utf8 = New-Object Text.UTF8Encoding($false)
try {
    $credentials = @{account=$account; password=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)}
    [IO.File]::WriteAllText((Join-Path $projectDir 'config/vendor-credentials.local.json'), ($credentials | ConvertTo-Json), $utf8)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    $credentials = $null
}
$cfg = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$settings = @{mode='vendor-http'; enabled=$true; projectId=$projectId; vendorBaseUrl='https://iot.wffdck.cn:1010'; credentialsFile='config/vendor-credentials.local.json'; vendorCacheFile='data/transmitter/vendor-http.json'; vendorPollIntervalMs=20000}
foreach ($name in $settings.Keys) { $cfg | Add-Member -NotePropertyName $name -NotePropertyValue $settings[$name] -Force }
[IO.File]::WriteAllText($configPath, ($cfg | ConvertTo-Json -Depth 10), $utf8)
Write-Host '已配置厂家直连，设备编号沿用原配置。请重新启动前端服务，并检查页面变送器连接状态。'
Write-Host '账号密码仅保存在本机配置文件，请勿分享该文件。原接入配置已备份。'
