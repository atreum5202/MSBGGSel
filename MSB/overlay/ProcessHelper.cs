using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace MSBOverlay;

/// <summary>Shared WinAPI imports used across multiple classes.</summary>
internal static class NativeImports
{
    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool IsIconic(IntPtr hWnd);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
}

/// <summary>Helpers for resolving PID from a CDP port and HWND from a PID.</summary>
public static class ProcessHelper
{
    /// <summary>
    /// Runs "netstat -ano" and finds the local address line containing :{port},
    /// then extracts the PID from the last column.
    /// </summary>
    public static int GetPidByPort(int port)
    {
        try
        {
            var psi = new ProcessStartInfo("netstat", "-ano")
            {
                RedirectStandardOutput = true,
                UseShellExecute = false,
                CreateNoWindow = true
            };

            using var proc = Process.Start(psi);
            if (proc == null) return 0;

            string output = proc.StandardOutput.ReadToEnd();
            proc.WaitForExit();

            string target = $":{port}";

            foreach (var line in output.Split('\n'))
            {
                // Example line:
                //   TCP    0.0.0.0:9222           0.0.0.0:0              LISTENING       1234
                var trimmed = line.Trim();
                if (!trimmed.StartsWith("TCP", StringComparison.OrdinalIgnoreCase) &&
                    !trimmed.StartsWith("UDP", StringComparison.OrdinalIgnoreCase))
                    continue;

                var parts = trimmed.Split(new char[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length < 2) continue;

                // parts[1] is the local address
                string localAddr = parts[1];
                if (!localAddr.Contains(target, StringComparison.OrdinalIgnoreCase)) continue;

                // PID is the last column
                if (int.TryParse(parts[^1], out int pid) && pid > 0)
                    return pid;
            }
        }
        catch
        {
            // ignore
        }

        return 0;
    }

    /// <summary>
    /// Enumerates all top-level windows and returns the first visible,
    /// non-empty-titled window belonging to the given PID.
    /// </summary>
    public static IntPtr GetMainWindowHandle(int pid)
    {
        IntPtr found = IntPtr.Zero;

        NativeImports.EnumWindows((hWnd, _) =>
        {
            if (!NativeImports.IsWindowVisible(hWnd)) return true;

            NativeImports.GetWindowThreadProcessId(hWnd, out uint windowPid);
            if ((int)windowPid != pid) return true;

            var sb = new StringBuilder(256);
            if (NativeImports.GetWindowText(hWnd, sb, 256) == 0) return true;
            if (sb.Length == 0) return true;

            found = hWnd;
            return false; // stop enumeration
        }, IntPtr.Zero);

        return found;
    }
}
