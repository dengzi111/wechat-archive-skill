# 微信图片救援：纯键盘 UI 自动化（不读内存、不注入、不改微信）
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File save_images_ui.ps1 -Count 200
#   powershell -ExecutionPolicy Bypass -File save_images_ui.ps1 -Count 200 -Direction next
#
# 重要: 微信「另存为」对话框会记住上一次选的目录。请先在微信里手动保存一张图片、
#       选定好目录（默认 %USERPROFILE%\Documents\微信图片导出），然后把这个目录作为 -SaveDir 传进来，
#       脚本靠该目录的文件数增量判断进度；两者不一致会导致进度判断失真。
#
# 原理: 看图窗口里  ←/→ 翻页 → Ctrl+S 调出保存对话框 → 回车 确认
#   * 一律用键盘快捷键，不用坐标点击（显示器缩放会让坐标整体偏移，肉眼看不出来）
#   * 没弹出对话框 = 这张图「已过期或被清理」→ 直接翻下一张
#   * 用保存目录的文件数增量判断进度；连续无新增 = 翻到头了

param(
    [int]$Count = 100,
    [int]$DelayMs = 1000,
    [string]$SaveDir = "",   # 留空 = %USERPROFILE%\Documents\微信图片导出
    [ValidateSet("prev", "next")][string]$Direction = "prev",
    [int]$StopAfterStuck = 10
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public class WxImg {
  public delegate bool E(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(E cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool f);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();

  public static bool DialogOpen() {
    bool f = false;
    EnumWindows(delegate(IntPtr h, IntPtr l) {
      if (!IsWindowVisible(h)) return true;
      StringBuilder c = new StringBuilder(128); GetClassName(h, c, 128);
      if (c.ToString() == "#32770") { f = true; return false; }   // 标准保存对话框
      return true;
    }, IntPtr.Zero);
    return f;
  }

  public static IntPtr Viewer() {
    IntPtr f = IntPtr.Zero;
    EnumWindows(delegate(IntPtr h, IntPtr l) {
      if (!IsWindowVisible(h)) return true;
      StringBuilder t = new StringBuilder(256); GetWindowText(h, t, 256);
      string s = t.ToString();
      if (s.Contains("图片和视频") || s.Contains("Image")) { f = h; return false; }
      return true;
    }, IntPtr.Zero);
    return f;
  }

  public static void Force(IntPtr h) {
    IntPtr fg = GetForegroundWindow(); uint p;
    uint tf = GetWindowThreadProcessId(fg, out p); uint tm = GetCurrentThreadId();
    AttachThreadInput(tm, tf, true); SetForegroundWindow(h); AttachThreadInput(tm, tf, false);
  }
}
"@

if ([string]::IsNullOrWhiteSpace($SaveDir)) { $SaveDir = Join-Path $env:USERPROFILE 'Documents\微信图片导出' }
if (-not (Test-Path $SaveDir)) { New-Item -ItemType Directory -Force -Path $SaveDir | Out-Null }
$key = if ($Direction -eq "prev") { "{LEFT}" } else { "{RIGHT}" }
$start = (Get-ChildItem $SaveDir -File -ErrorAction SilentlyContinue).Count
Write-Host "开始: 目录已有 $start 个文件, 方向=$Direction, 目标 $Count 张"

$viewer = [WxImg]::Viewer()
$saved = 0; $skip = 0; $stuck = 0; $last = $start

for ($i = 1; $i -le $Count; $i++) {
    if ($viewer -ne [IntPtr]::Zero) { [WxImg]::Force($viewer); Start-Sleep -Milliseconds 150 }
    [System.Windows.Forms.SendKeys]::SendWait($key)
    Start-Sleep -Milliseconds $DelayMs
    [System.Windows.Forms.SendKeys]::SendWait("^s")          # Ctrl+S
    Start-Sleep -Milliseconds 1200
    if ([WxImg]::DialogOpen()) {
        [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
        Start-Sleep -Milliseconds 850
        if ([WxImg]::DialogOpen()) { [System.Windows.Forms.SendKeys]::SendWait("{ENTER}"); Start-Sleep -Milliseconds 600 }
        $saved++
    } else { $skip++ }                                        # 图片已过期或被清理

    $now = (Get-ChildItem $SaveDir -File -ErrorAction SilentlyContinue).Count
    if ($now -eq $last) { $stuck++ } else { $stuck = 0; $last = $now }
    if ($stuck -ge $StopAfterStuck) { Write-Host "连续 $stuck 次无新文件 -> 翻到头，停止"; break }
    if ($i % 20 -eq 0) { Write-Host "  $i/$Count  保存 $saved  跳过 $skip  文件 $now" }
}

$end = (Get-ChildItem $SaveDir -File -ErrorAction SilentlyContinue).Count
Write-Host "完成: 保存 $saved 张, 跳过(过期) $skip 张, 目录 $start -> $end"
