param(
    [Parameter(Mandatory)][string]$OutDir,
    [int]$Minutes = 10,
    [int]$IntervalMs = 1500
)
# Capture only Xenia's own window (PrintWindow on its handle; nothing else on screen), keeping a frame only
# when the picture changed. Skips minimized or near-black frames. Research use: screens of the game's UI.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System; using System.Runtime.InteropServices;
public static class XeniaWindow { [StructLayout(LayoutKind.Sequential)] public struct R { public int L,T,Ri,B; }
[DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out R r);
[DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
[DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
[DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }
'@
[XeniaWindow]::SetProcessDPIAware() | Out-Null
New-Item -ItemType Directory -Force $OutDir | Out-Null
$end = (Get-Date).AddMinutes($Minutes); $last = $null; $n = 0
while ((Get-Date) -lt $end) {
    $p = Get-Process xenia_canary* -ErrorAction SilentlyContinue | Where-Object MainWindowHandle -ne 0 | Select-Object -First 1
    if ($p) {
        $h = $p.MainWindowHandle; $r = New-Object XeniaWindow+R; [XeniaWindow]::GetClientRect($h, [ref]$r) | Out-Null
        if (-not [XeniaWindow]::IsIconic($h) -and $r.Ri -gt 0 -and $r.B -gt 0) {
            $bmp = New-Object System.Drawing.Bitmap ($r.Ri), ($r.B); $g = [System.Drawing.Graphics]::FromImage($bmp); $dc = $g.GetHdc()
            [XeniaWindow]::PrintWindow($h, $dc, 3) | Out-Null; $g.ReleaseHdc($dc); $g.Dispose()
            # a small signature: brightness of a coarse grid, to drop repeats and black frames
            $sig = New-Object System.Text.StringBuilder; $bright = 0
            for ($x = 40; $x -lt $bmp.Width; $x += 160) { for ($y = 40; $y -lt $bmp.Height; $y += 120) {
                $c = $bmp.GetPixel($x, $y); $v = [int](($c.R + $c.G + $c.B) / 24); $bright += $v; [void]$sig.Append([char](65 + $v)) } }
            $s = $sig.ToString()
            if ($bright -gt 40 -and $s -ne $last) {
                $n++; $bmp.Save((Join-Path $OutDir ('{0:D3}-{1}.png' -f $n, (Get-Date -Format HHmmss))), [System.Drawing.Imaging.ImageFormat]::Png); $last = $s
            }
            $bmp.Dispose()
        }
    }
    Start-Sleep -Milliseconds $IntervalMs
}
"captured $n frames in $OutDir"
