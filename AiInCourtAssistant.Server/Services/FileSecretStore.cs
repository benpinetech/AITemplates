using System.Text.Json;
using Microsoft.AspNetCore.DataProtection;

namespace AiInCourtAssistant.Server.Services;

public interface ISecretStore
{
    Task SetAsync(string name, string value, CancellationToken ct = default);
    Task<string?> GetAsync(string name, CancellationToken ct = default);
    Task<(bool exists, string? last4, DateTimeOffset? updatedAt)> GetMetaAsync(string name, CancellationToken ct = default);
}

/// Encrypts values with ASP.NET Core Data Protection and stores them in App_Data/secrets.json
public sealed class FileSecretStore : ISecretStore
{
    private readonly IDataProtector _protector;
    private readonly string _filePath;
    private readonly SemaphoreSlim _gate = new(1, 1);

    private sealed class SecretRecord
    {
        public string Protected { get; set; } = "";
        public string? Last4 { get; set; }
        public DateTimeOffset UpdatedAt { get; set; }
    }

    public FileSecretStore(IDataProtectionProvider dp, IWebHostEnvironment env)
    {
        _protector = dp.CreateProtector("AiInCourtAssistant.SecretStore.v1");
        var dataDir = Path.Combine(env.ContentRootPath, "App_Data");
        Directory.CreateDirectory(dataDir);
        _filePath = Path.Combine(dataDir, "secrets.json");
    }

    public async Task SetAsync(string name, string value, CancellationToken ct = default)
    {
        var last4 = value.Length >= 4 ? value[^4..] : value;
        var rec = new SecretRecord { Protected = _protector.Protect(value), Last4 = last4, UpdatedAt = DateTimeOffset.UtcNow };

        await _gate.WaitAsync(ct);
        try
        {
            var all = await ReadAllAsync(ct);
            all[name] = rec;
            await WriteAllAsync(all, ct);
        }
        finally { _gate.Release(); }
    }

    public async Task<string?> GetAsync(string name, CancellationToken ct = default)
    {
        await _gate.WaitAsync(ct);
        try
        {
            var all = await ReadAllAsync(ct);
            if (!all.TryGetValue(name, out var rec)) return null;
            return _protector.Unprotect(rec.Protected);
        }
        finally { _gate.Release(); }
    }

    public async Task<(bool exists, string? last4, DateTimeOffset? updatedAt)> GetMetaAsync(string name, CancellationToken ct = default)
    {
        await _gate.WaitAsync(ct);
        try
        {
            var all = await ReadAllAsync(ct);
            if (!all.TryGetValue(name, out var rec)) return (false, null, null);
            return (true, rec.Last4, rec.UpdatedAt);
        }
        finally { _gate.Release(); }
    }

    private async Task<Dictionary<string, SecretRecord>> ReadAllAsync(CancellationToken ct)
    {
        if (!File.Exists(_filePath)) return new();
        using var fs = File.OpenRead(_filePath);
        return await JsonSerializer.DeserializeAsync<Dictionary<string, SecretRecord>>(fs, cancellationToken: ct) ?? new();
    }

    private async Task WriteAllAsync(Dictionary<string, SecretRecord> map, CancellationToken ct)
    {
        using var fs = File.Create(_filePath);
        await JsonSerializer.SerializeAsync(fs, map, new JsonSerializerOptions { WriteIndented = true }, ct);
    }
}
