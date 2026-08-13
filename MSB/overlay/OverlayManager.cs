using System.Diagnostics;
using System.Windows;
using Application = System.Windows.Application;

namespace MSBOverlay;

/// <summary>
/// Manages the lifecycle of overlay windows keyed by profile ID.
/// Called on every poll tick to create/update/destroy overlays.
/// </summary>
public sealed class OverlayManager : IDisposable
{
    private readonly MsbApiClient _api;

    // profileId → OverlayWindow
    private readonly Dictionary<string, OverlayWindow> _overlays = new();

    // Guard against concurrent ticks
    private int _tickRunning = 0;

    public OverlayManager(MsbApiClient api)
    {
        _api = api;
    }

    public async Task TickAsync()
    {
        // Prevent overlapping ticks
        if (System.Threading.Interlocked.Exchange(ref _tickRunning, 1) == 1)
            return;

        try
        {
            var profiles      = await _api.GetProfilesAsync();
            var browserStatus = await _api.GetBrowserStatusAsync();

            // Build number map: profileId → displayNumber (index+1 if number==null)
            var numberMap = new Dictionary<string, int>();
            for (int i = 0; i < profiles.Count; i++)
            {
                var p = profiles[i];
                numberMap[p.Id] = p.Number ?? (i + 1);
            }

            // Build email map
            var emailMap = profiles.ToDictionary(
                p => p.Id,
                p => p.Account?.Email ?? string.Empty);

            // Determine which profile IDs are currently running
            var runningIds = new HashSet<string>(browserStatus.Select(b => b.Id));

            // Remove overlays for profiles that are no longer running
            var toRemove = _overlays.Keys.Where(id => !runningIds.Contains(id)).ToList();
            foreach (var id in toRemove)
                DestroyOverlay(id);

            // Create or update overlays for running profiles
            foreach (var status in browserStatus)
            {
                int pid  = ProcessHelper.GetPidByPort(status.CdpPort);
                if (pid == 0) continue;

                // Verify process still alive
                if (!IsProcessAlive(pid))
                {
                    DestroyOverlay(status.Id);
                    continue;
                }

                IntPtr hwnd = ProcessHelper.GetMainWindowHandle(pid);
                if (hwnd == IntPtr.Zero) continue;

                int    number = numberMap.TryGetValue(status.Id, out var n) ? n : 0;
                string email  = emailMap.TryGetValue(status.Id, out var e)  ? e : string.Empty;

                await Application.Current.Dispatcher.InvokeAsync(() =>
                {
                    if (_overlays.TryGetValue(status.Id, out var existing))
                    {
                        existing.UpdateTarget(hwnd);
                    }
                    else
                    {
                        var overlay = new OverlayWindow(number, email, hwnd);
                        _overlays[status.Id] = overlay;
                        overlay.Show();
                    }
                });
            }
        }
        finally
        {
            System.Threading.Interlocked.Exchange(ref _tickRunning, 0);
        }
    }

    private void DestroyOverlay(string profileId)
    {
        if (!_overlays.TryGetValue(profileId, out var overlay))
            return;

        _overlays.Remove(profileId);

        Application.Current?.Dispatcher.Invoke(() =>
        {
            try { overlay.Close(); }
            catch { /* already closed */ }
        });
    }

    private static bool IsProcessAlive(int pid)
    {
        try
        {
            var proc = Process.GetProcessById(pid);
            return !proc.HasExited;
        }
        catch
        {
            return false;
        }
    }

    public void Dispose()
    {
        Application.Current?.Dispatcher.Invoke(() =>
        {
            foreach (var overlay in _overlays.Values)
            {
                try { overlay.Close(); }
                catch { /* ignore */ }
            }
            _overlays.Clear();
        });

        _api.Dispose();
    }
}
