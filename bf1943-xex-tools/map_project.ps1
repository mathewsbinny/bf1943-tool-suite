param([switch]$ValidateOnly, [ValidatePattern('^[A-Za-z0-9_]+\.java$')][string]$Script = 'MapXex.java')
$ErrorActionPreference = 'Stop'
$projectRoot = (& "$PSScriptRoot/workspace.ps1")
$pilotRoot = "$projectRoot/work/xex-analysis"
$pilotArgs = @("-Duser.home=$pilotRoot/home", "-Djava.io.tmpdir=$pilotRoot/tmp", "-Dapplication.tempdir=$pilotRoot/tmp", "-Dapplication.cachedir=$pilotRoot/cache", "-Dapplication.settingsdir=$pilotRoot/settings", '-Djava.system.class.loader=ghidra.GhidraClassLoader', '-cp', 'C:/Tools/ghidra_12.1.4_PUBLIC/Ghidra/Framework/Utility/lib/Utility.jar', 'ghidra.Ghidra', 'ghidra.app.util.headless.AnalyzeHeadless', "$pilotRoot/project", 'BF1943-entry-pilot', '-process', 'default.memory.bin', '-noanalysis', '-scriptPath', $PSScriptRoot, '-postScript', 'MapXex.java', $pilotRoot, '-log', "$pilotRoot/mapping-ghidra.log", '-scriptlog', "$pilotRoot/mapping-script.log")
$ErrorActionPreference = 'Continue'
$pilotArgs[$pilotArgs.IndexOf('MapXex.java')] = $Script
if ($ValidateOnly) {
    $pilotArgs[$pilotArgs.IndexOf($Script)] = 'ValidateXex.java'
    $pilotArgs += '-readOnly'
}
$logStem = if ($ValidateOnly) { 'ValidateXex' } else { [IO.Path]::GetFileNameWithoutExtension($Script) }
$pilotArgs[$pilotArgs.IndexOf('-log') + 1] = "$pilotRoot/$logStem-ghidra.log"
$pilotArgs[$pilotArgs.IndexOf('-scriptlog') + 1] = "$pilotRoot/$logStem-script.log"
& 'C:/Program Files/Java/jdk-26.0.2/bin/java.exe' @pilotArgs *> "$pilotRoot/$logStem-console.log"
if ($LASTEXITCODE -ne 0) { throw "Ghidra failed; inspect $pilotRoot/$logStem-console.log" }
