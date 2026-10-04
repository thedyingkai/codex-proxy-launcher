param(
    [ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version = '1.0.0',
    [string]$RuntimeArchive = ''
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$distRoot = Join-Path $repoRoot 'dist'
$packageRoot = Join-Path $distRoot 'CodexProxyLauncher'
$zipPath = Join-Path $distRoot "codex-proxy-launcher-$Version-windows-x64.zip"
if ((Test-Path -LiteralPath $packageRoot) -or (Test-Path -LiteralPath $zipPath)) {
    throw 'A package already exists in dist. Use a clean build directory before packaging again.'
}
$manifest = Get-Content -LiteralPath (Join-Path $repoRoot 'runtime-source.json') -Raw | ConvertFrom-Json
$runtimeUri = [Uri]$manifest.url
if ($runtimeUri.Scheme -ne 'https' -or $runtimeUri.Host -ne 'www.python.org') {
    throw 'Runtime URL must use HTTPS on www.python.org.'
}
if (-not $RuntimeArchive) {
    $cacheRoot = Join-Path $repoRoot '.build-cache'
    New-Item -ItemType Directory -Path $cacheRoot -Force | Out-Null
    $RuntimeArchive = Join-Path $cacheRoot 'python-embed.zip'
    if (-not (Test-Path -LiteralPath $RuntimeArchive)) {
        Invoke-WebRequest -Uri $manifest.url -OutFile $RuntimeArchive -UseBasicParsing
    }
}
$actualHash = (Get-FileHash -LiteralPath $RuntimeArchive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $manifest.sha256) { throw 'Runtime SHA-256 verification failed.' }
& (Join-Path $repoRoot 'src\build.ps1')
New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'src') -Force | Out-Null
Expand-Archive -LiteralPath $RuntimeArchive -DestinationPath (Join-Path $packageRoot 'runtime')
foreach ($name in @('backend.py', 'Launcher.cs', 'build.ps1', 'test_backend.py')) {
    Copy-Item -LiteralPath (Join-Path $repoRoot "src\$name") -Destination (Join-Path $packageRoot "src\$name")
}
foreach ($name in @('CodexProxyLauncher.exe', 'README.md', 'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md', 'runtime-source.json')) {
    Copy-Item -LiteralPath (Join-Path $repoRoot $name) -Destination (Join-Path $packageRoot $name)
}
Copy-Item -LiteralPath (Join-Path $repoRoot 'settings.example.json') -Destination (Join-Path $packageRoot 'settings.json')
$checksums = [ordered]@{}
Get-ChildItem -LiteralPath $packageRoot -Recurse -File | Sort-Object FullName | ForEach-Object {
    $relativePath = $_.FullName.Substring($packageRoot.Length + 1).Replace('\', '/')
    $checksums[$relativePath] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $packageRoot 'SHA256SUMS.json'), ($checksums | ConvertTo-Json -Depth 5), $utf8)
Compress-Archive -LiteralPath $packageRoot -DestinationPath $zipPath -CompressionLevel Optimal
$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText(($zipPath + '.sha256'), ($zipHash + '  ' + [IO.Path]::GetFileName($zipPath) + "`n"), $utf8)
Write-Output "Package: $zipPath"
Write-Output "SHA-256: $zipHash"
