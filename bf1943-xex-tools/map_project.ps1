param([switch]$ValidateOnly, [ValidatePattern('^[A-Za-z0-9_]+\.java$')][string]$Script = 'MapXex.java')
$ErrorActionPreference = 'Stop'
$projectRoot = (& "$PSScriptRoot/workspace.ps1")
$pilotRoot = "$projectRoot/work/xex-analysis"
# the pinned Java and Ghidra installs (BF1943_JAVA and BF1943_GHIDRA name others: the tests' stand-ins)
$java = if ($env:BF1943_JAVA) { $env:BF1943_JAVA } else { 'C:/Program Files/Java/jdk-26.0.2/bin/java.exe' }
$ghidra = if ($env:BF1943_GHIDRA) { $env:BF1943_GHIDRA } else { 'C:/Tools/ghidra_12.1.4_PUBLIC' }
$pilotArgs = @("-Duser.home=$pilotRoot/home", "-Djava.io.tmpdir=$pilotRoot/tmp", "-Dapplication.tempdir=$pilotRoot/tmp", "-Dapplication.cachedir=$pilotRoot/cache", "-Dapplication.settingsdir=$pilotRoot/settings", '-Djava.system.class.loader=ghidra.GhidraClassLoader', '-cp', "$ghidra/Ghidra/Framework/Utility/lib/Utility.jar", 'ghidra.Ghidra', 'ghidra.app.util.headless.AnalyzeHeadless', "$pilotRoot/project", 'BF1943-entry-pilot', '-process', 'default.memory.bin', '-noanalysis', '-scriptPath', $PSScriptRoot, '-postScript', 'MapXex.java', $pilotRoot, '-log', "$pilotRoot/mapping-ghidra.log", '-scriptlog', "$pilotRoot/mapping-script.log")
$pilotArgs[$pilotArgs.IndexOf('MapXex.java')] = $Script
if ($ValidateOnly) {
    $pilotArgs[$pilotArgs.IndexOf($Script)] = 'ValidateXex.java'
    $pilotArgs += '-readOnly'
}
$logStem = if ($ValidateOnly) { 'ValidateXex' } else { [IO.Path]::GetFileNameWithoutExtension($Script) }
$pilotArgs[$pilotArgs.IndexOf('-log') + 1] = "$pilotRoot/$logStem-ghidra.log"
$pilotArgs[$pilotArgs.IndexOf('-scriptlog') + 1] = "$pilotRoot/$logStem-script.log"
# Ghidra's headless analyzer exits 0 when a post-script throws, so the exit code alone cannot tell a failed script:
# the script's own result must be written by this run (ValidateXex.java: saved-validation.txt, starting PASS, and only
# on a pass; MapXex.java: mapping-report.txt). A result left from an earlier run is removed first, so a failure cannot
# leave an old PASS behind for record_batch_decompile.py.
$result = if ($ValidateOnly) { "$pilotRoot/saved-validation.txt" } elseif ($Script -eq 'MapXex.java') { "$pilotRoot/mapping-report.txt" } else { $null }
if ($result -and (Test-Path -LiteralPath $result)) { Remove-Item -LiteralPath $result }
$started = Get-Date
$ErrorActionPreference = 'Continue'
& $java @pilotArgs *> "$pilotRoot/$logStem-console.log"
$exit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($exit -ne 0) { throw "Ghidra failed; inspect $pilotRoot/$logStem-console.log" }
if ($result) {
    if (!(Test-Path -LiteralPath $result) -or (Get-Item -LiteralPath $result).LastWriteTime -lt $started) {
        throw "$logStem did not complete (no $result from this run); inspect $pilotRoot/$logStem-script.log"
    }
    if ($ValidateOnly -and !((Get-Content -LiteralPath $result -TotalCount 1) -match '^PASS ')) {
        throw "$logStem did not pass: $(Get-Content -LiteralPath $result -TotalCount 1)"
    }
}
