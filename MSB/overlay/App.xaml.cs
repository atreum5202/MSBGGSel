using System.Drawing;
using System.Windows;
using System.Windows.Forms;
using Application = System.Windows.Application;

namespace MSBOverlay;

public partial class App : Application
{
    private NotifyIcon? _trayIcon;
    private OverlayManager? _overlayManager;
    private System.Threading.Timer? _pollTimer;

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        ShutdownMode = ShutdownMode.OnExplicitShutdown;

        _trayIcon = new NotifyIcon
        {
            Icon = SystemIcons.Application,
            Visible = true,
            Text = "MSB Overlay"
        };

        var contextMenu = new ContextMenuStrip();
        var exitItem = new ToolStripMenuItem("Exit");
        exitItem.Click += (_, _) => ExitApp();
        contextMenu.Items.Add(exitItem);
        _trayIcon.ContextMenuStrip = contextMenu;

        var apiClient = new MsbApiClient("http://127.0.0.1:17248");
        _overlayManager = new OverlayManager(apiClient);

        _pollTimer = new System.Threading.Timer(
            async _ => await _overlayManager.TickAsync(),
            null,
            TimeSpan.Zero,
            TimeSpan.FromSeconds(3));
    }

    private void ExitApp()
    {
        _pollTimer?.Dispose();
        _overlayManager?.Dispose();
        _trayIcon?.Dispose();
        Shutdown();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _trayIcon?.Dispose();
        base.OnExit(e);
    }
}
