using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;

namespace MSBOverlay;

public partial class OverlayWindow : Window
{
    // ── WinAPI ──────────────────────────────────────────────────────────────

    private delegate void WinEventDelegate(
        IntPtr hWinEventHook, uint eventType,
        IntPtr hwnd, int idObject, int idChild,
        uint dwEventThread, uint dwmsEventTime);

    [DllImport("user32.dll")]
    private static extern IntPtr SetWinEventHook(
        uint eventMin, uint eventMax,
        IntPtr hmodWinEventProc, WinEventDelegate lpfnWinEventProc,
        uint idProcess, uint idThread, uint dwFlags);

    [DllImport("user32.dll")]
    private static extern bool UnhookWinEvent(IntPtr hWinEventHook);

    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr hwnd, out RECT lpRect);

    [DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr hwnd);

    [DllImport("user32.dll")]
    private static extern bool IsWindow(IntPtr hwnd);

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int Left, Top, Right, Bottom; }

    private const uint EVENT_OBJECT_LOCATIONCHANGE = 0x800B;
    private const uint EVENT_SYSTEM_MINIMIZESTART   = 0x0016;
    private const uint EVENT_SYSTEM_MINIMIZEEND     = 0x0017;
    private const uint WINEVENT_OUTOFCONTEXT        = 0x0000;

    // ── Fields ───────────────────────────────────────────────────────────────

    private IntPtr _targetHwnd;
    private IntPtr _hookHandle;
    private WinEventDelegate? _hookDelegate; // keep GC-alive reference

    // ── Constructor ──────────────────────────────────────────────────────────

    public OverlayWindow(int profileNumber, string email, IntPtr targetHwnd)
    {
        InitializeComponent();
        BadgeLabel.Text = $"#{profileNumber} | {email}";
        _targetHwnd = targetHwnd;

        Loaded += OnLoaded;
        Closed += OnClosed;
    }

    // ── Lifecycle ────────────────────────────────────────────────────────────

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        PositionOverlay();
        InstallHook();
    }

    private void OnClosed(object? sender, EventArgs e)
    {
        RemoveHook();
    }

    // ── Public API ───────────────────────────────────────────────────────────

    /// <summary>Called on each poll tick to reposition and sync visibility.</summary>
    public void UpdateTarget(IntPtr newHwnd)
    {
        if (_targetHwnd != newHwnd)
        {
            RemoveHook();
            _targetHwnd = newHwnd;
            InstallHook();
        }

        SyncVisibility();
        if (IsVisible) PositionOverlay();
    }

    // ── Positioning ──────────────────────────────────────────────────────────

    private void PositionOverlay()
    {
        if (_targetHwnd == IntPtr.Zero) return;
        if (!IsWindow(_targetHwnd)) return;

        if (GetWindowRect(_targetHwnd, out RECT r))
        {
            Left = r.Left + 8;
            Top  = r.Top  + 8;
        }
    }

    private void SyncVisibility()
    {
        if (_targetHwnd == IntPtr.Zero || !IsWindow(_targetHwnd))
        {
            Hide();
            return;
        }

        if (IsIconic(_targetHwnd))
            Hide();
        else
            Show();
    }

    // ── WinEventHook ─────────────────────────────────────────────────────────

    private void InstallHook()
    {
        if (_targetHwnd == IntPtr.Zero) return;

        _hookDelegate = OnWinEvent; // prevent GC collection
        _hookHandle = SetWinEventHook(
            EVENT_OBJECT_LOCATIONCHANGE,
            EVENT_SYSTEM_MINIMIZEEND,
            IntPtr.Zero,
            _hookDelegate,
            (uint)GetTargetPid(),
            0,
            WINEVENT_OUTOFCONTEXT);
    }

    private void RemoveHook()
    {
        if (_hookHandle != IntPtr.Zero)
        {
            UnhookWinEvent(_hookHandle);
            _hookHandle = IntPtr.Zero;
        }
    }

    private int GetTargetPid()
    {
        if (_targetHwnd == IntPtr.Zero) return 0;
        NativeImports.GetWindowThreadProcessId(_targetHwnd, out uint pid);
        return (int)pid;
    }

    private void OnWinEvent(
        IntPtr hWinEventHook, uint eventType,
        IntPtr hwnd, int idObject, int idChild,
        uint dwEventThread, uint dwmsEventTime)
    {
        if (hwnd != _targetHwnd) return;

        Dispatcher.BeginInvoke(() =>
        {
            switch (eventType)
            {
                case EVENT_OBJECT_LOCATIONCHANGE:
                    if (!IsIconic(_targetHwnd)) PositionOverlay();
                    break;
                case EVENT_SYSTEM_MINIMIZESTART:
                    Hide();
                    break;
                case EVENT_SYSTEM_MINIMIZEEND:
                    Show();
                    PositionOverlay();
                    break;
            }
        });
    }
}
