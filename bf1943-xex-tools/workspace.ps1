# The research workspace the executable tools read and write (see workspace.py): BF1943_RESEARCH, else the
# current folder. It must hold work\ and research\. Prints the root; exits 1 with what to do otherwise.
$root = if ($env:BF1943_RESEARCH) { $env:BF1943_RESEARCH } else { (Get-Location).Path }
$root = (Resolve-Path -LiteralPath $root).Path
if (-not ((Test-Path -LiteralPath "$root/work" -PathType Container) -and (Test-Path -LiteralPath "$root/research" -PathType Container))) {
    Write-Error "$root is not a research workspace (it needs work\ and research\): run the tool from one, such as operation-rising-sun, or set BF1943_RESEARCH to it"
    exit 1
}
$root
