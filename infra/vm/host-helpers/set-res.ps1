param([int]$W = 1600, [int]$H = 900, [int]$BPP = 32)
# Set the guest display resolution via ChangeDisplaySettingsEx. Used to lock the
# client geometry (1600x900) that all pixel sensors/fingerprints derive from.
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Disp {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
  public struct DEVMODE {
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmDeviceName;
    public ushort dmSpecVersion; public ushort dmDriverVersion; public ushort dmSize;
    public ushort dmDriverExtra; public uint dmFields;
    public int dmPositionX; public int dmPositionY; public uint dmDisplayOrientation; public uint dmDisplayFixedOutput;
    public short dmColor; public short dmDuplex; public short dmYResolution; public short dmTTOption; public short dmCollate;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmFormName;
    public ushort dmLogPixels; public uint dmBitsPerPel; public uint dmPelsWidth; public uint dmPelsHeight;
    public uint dmDisplayFlags; public uint dmDisplayFrequency;
    public uint dmICMMethod; public uint dmICMIntent; public uint dmMediaType; public uint dmDitherType;
    public uint dmReserved1; public uint dmReserved2; public uint dmPanningWidth; public uint dmPanningHeight;
  }
  [DllImport("user32.dll")] public static extern int EnumDisplaySettings(string d, int m, ref DEVMODE dm);
  [DllImport("user32.dll")] public static extern int ChangeDisplaySettingsEx(string d, ref DEVMODE dm, IntPtr h, uint flags, IntPtr l);
}
"@
$dm = New-Object Disp+DEVMODE
$dm.dmSize = [Runtime.InteropServices.Marshal]::SizeOf($dm)
[void][Disp]::EnumDisplaySettings($null, -1, [ref]$dm)   # ENUM_CURRENT_SETTINGS

# enumerate available modes to see if the target is offered by the driver
$avail = @(); $i = 0
$m = New-Object Disp+DEVMODE; $m.dmSize = [Runtime.InteropServices.Marshal]::SizeOf($m)
while ([Disp]::EnumDisplaySettings($null, $i, [ref]$m)) { $avail += "$($m.dmPelsWidth)x$($m.dmPelsHeight)"; $i++ }
$has = ($avail | Select-Object -Unique) -contains "${W}x${H}"

$dm.dmPelsWidth = $W; $dm.dmPelsHeight = $H; $dm.dmBitsPerPel = $BPP
$dm.dmFields = 0x40000 -bor 0x80000 -bor 0x100000        # BPP | WIDTH | HEIGHT
$rc = [Disp]::ChangeDisplaySettingsEx($null, [ref]$dm, [IntPtr]::Zero, 0x01, [IntPtr]::Zero)  # CDS_UPDATEREGISTRY
$out = @(
  "target=${W}x${H} offered=$has rc=$rc (0=DISP_CHANGE_SUCCESSFUL)"
  "modes: " + (($avail | Select-Object -Unique) -join ', ')
) -join "`r`n"
# Log so a session-1 scheduled-task run can report back.
$out | Out-File C:\ib\res.log -Encoding ascii
$out
