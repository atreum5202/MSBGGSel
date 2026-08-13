using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace MSBOverlay;

// ── DTOs ────────────────────────────────────────────────────────────────────

public record ProfileDto(
    [property: JsonPropertyName("id")]     string Id,
    [property: JsonPropertyName("name")]   string? Name,
    [property: JsonPropertyName("number")] int? Number,
    [property: JsonPropertyName("account")] AccountDto? Account);

public record AccountDto(
    [property: JsonPropertyName("email")] string? Email);

public record BrowserStatusDto(
    [property: JsonPropertyName("id")]      string Id,
    [property: JsonPropertyName("engine")]  string? Engine,
    [property: JsonPropertyName("cdpPort")] int CdpPort);

internal record ApiResponse<T>(
    [property: JsonPropertyName("ok")]   bool Ok,
    [property: JsonPropertyName("data")] T? Data);

// ── Client ───────────────────────────────────────────────────────────────────

public sealed class MsbApiClient : IDisposable
{
    private readonly HttpClient _http;

    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNameCaseInsensitive = true
    };

    public MsbApiClient(string baseUrl)
    {
        _http = new HttpClient
        {
            BaseAddress = new Uri(baseUrl),
            Timeout = TimeSpan.FromSeconds(5)
        };
    }

    /// <summary>Returns all profiles from /profiles.</summary>
    public async Task<List<ProfileDto>> GetProfilesAsync()
    {
        try
        {
            var json = await _http.GetStringAsync("/profiles");
            var resp = JsonSerializer.Deserialize<ApiResponse<List<ProfileDto>>>(json, JsonOpts);
            return resp?.Data ?? [];
        }
        catch
        {
            return [];
        }
    }

    /// <summary>Returns currently running browser sessions from /browser/status.</summary>
    public async Task<List<BrowserStatusDto>> GetBrowserStatusAsync()
    {
        try
        {
            var json = await _http.GetStringAsync("/browser/status");
            var resp = JsonSerializer.Deserialize<ApiResponse<List<BrowserStatusDto>>>(json, JsonOpts);
            return resp?.Data ?? [];
        }
        catch
        {
            return [];
        }
    }

    public void Dispose() => _http.Dispose();
}
