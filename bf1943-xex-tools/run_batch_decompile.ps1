param([int]$Limit = 0, [ValidatePattern('^[A-Za-z0-9_]+\.java$')][string]$Script = 'export_all_decompiled.java', [string]$Functions = '')
$ErrorActionPreference = 'Stop'
$root = (& "$PSScriptRoot/workspace.ps1")
$pilotRoot = "$root/work/xex-analysis"
$module = "$root/work/vmx128/XenonVMX128"
if (!(Test-Path "$module/data/languages/xenon.sla")) { throw 'Build the Xenon language first (run_vmx.ps1)' }
# The saved main project must be byte-identical afterwards; -readOnly discards every in-session change.
function Get-ProjectIdentity { Get-ChildItem -LiteralPath "$pilotRoot/project" -Recurse -File | Where-Object { $_.Extension -ne '.lock' -and $_.Name -notlike '*.lock~' } | Sort-Object FullName | ForEach-Object { "$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash) $($_.FullName.Substring($pilotRoot.Length))" } }
$before = Get-ProjectIdentity
New-Item -ItemType Directory -Force "$pilotRoot/batch-scripts" | Out-Null
Copy-Item -LiteralPath "$PSScriptRoot/$Script" -Destination "$pilotRoot/batch-scripts/$Script"
$javaArgs = @('-Xmx8g', "-Duser.home=$pilotRoot/home", "-Djava.io.tmpdir=$pilotRoot/tmp", "-Dapplication.tempdir=$pilotRoot/tmp", "-Dapplication.cachedir=$pilotRoot/cache", "-Dapplication.settingsdir=$pilotRoot/settings", "-Dghidra.external.modules=$module", '-Djava.system.class.loader=ghidra.GhidraClassLoader', '-cp', 'C:/Tools/ghidra_12.1.4_PUBLIC/Ghidra/Framework/Utility/lib/Utility.jar', 'ghidra.Ghidra', 'ghidra.app.util.headless.AnalyzeHeadless', "$pilotRoot/project", 'BF1943-entry-pilot', '-process', 'default.memory.bin', '-readOnly', '-noanalysis', '-scriptPath', "$pilotRoot/batch-scripts", '-postScript', $Script, $pilotRoot)
if ($Functions) { $javaArgs += $Functions } elseif ($Limit -gt 0) { $javaArgs += "$Limit" }
$javaArgs += @('-log', "$pilotRoot/batch-ghidra.log", '-scriptlog', "$pilotRoot/batch-script.log")
$ErrorActionPreference = 'Continue'
$started = Get-Date
& 'C:/Program Files/Java/jdk-26.0.2/bin/java.exe' @javaArgs *> "$pilotRoot/batch-console.log"
if ($LASTEXITCODE -ne 0) { throw "Ghidra failed; inspect $pilotRoot/batch-console.log" }
$after = Get-ProjectIdentity
if (Compare-Object $before $after) { throw 'Saved main project changed during read-only batch' }
$before | Set-Content -Encoding utf8 "$pilotRoot/batch-project-identity.txt"
if ($Script -ne 'export_all_decompiled.java') { return }
$summary = "$pilotRoot/batch-decompile.txt"
if (!(Test-Path $summary) -or (Get-Item $summary).LastWriteTime -lt $started) { throw 'Batch summary not written; inspect batch-console.log' }
Get-Content $summary
"elapsed=$((Get-Date) - $started)"
