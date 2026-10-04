$ErrorActionPreference = 'Stop'
$packageRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$source = Join-Path $PSScriptRoot 'Launcher.cs'
$outputExe = Join-Path $packageRoot 'CodexProxyLauncher.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /utf8output "/out:$outputExe" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll $source
if ($LASTEXITCODE -ne 0) { throw 'Compilation failed.' }
Get-FileHash -LiteralPath $outputExe -Algorithm SHA256
$bridge = Join-Path $packageRoot 'CodexNativeProxy.exe'
& $compiler /nologo /target:exe /platform:x64 /optimize+ /utf8output "/out:$bridge" (Join-Path $PSScriptRoot 'NativeProxy.cs')
if ($LASTEXITCODE -ne 0) { throw 'Native bridge compilation failed.' }
Get-FileHash -LiteralPath $bridge -Algorithm SHA256
$cloud = Join-Path $packageRoot 'CodexCloudProxy.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /utf8output "/out:$cloud" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll (Join-Path $PSScriptRoot 'CloudProxy.cs')
if ($LASTEXITCODE -ne 0) { throw 'Cloud network helper compilation failed.' }
Get-FileHash -LiteralPath $cloud -Algorithm SHA256
