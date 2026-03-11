// Server/Features/Live/Deepgram/DeepgramStreamFactory.cs
using System.Net.WebSockets;
using Microsoft.Extensions.Options;
using AiInCourtAssistant.Server.Services;

namespace AiInCourtAssistant.Server.Features.Live.Deepgram
{
    public interface IDeepgramStreamFactory
    {
        Task<ClientWebSocket> CreateAsync(CancellationToken ct);
        Uri BuildUri();
    }

    public sealed class DeepgramStreamFactory : IDeepgramStreamFactory
    {
        private readonly DeepgramOptions _opts;
        private readonly ISecretStore _secrets;
        private readonly ILogger<DeepgramStreamFactory> _log;

        public DeepgramStreamFactory(
            IOptions<DeepgramOptions> opts,
            ISecretStore secrets,
            ILogger<DeepgramStreamFactory> log)
        {
            _opts = opts.Value;
            _secrets = secrets;
            _log = log;
        }

        public Uri BuildUri() => _opts.BuildUri();

        public async Task<ClientWebSocket> CreateAsync(CancellationToken ct)
        {
            // Resolve API key in this order: env var -> appsettings -> secret store (Admin /deepgram)
            var apiKey = Environment.GetEnvironmentVariable("DEEPGRAM_API_KEY");
            if (string.IsNullOrWhiteSpace(apiKey))
                apiKey = _opts.ApiKey; // from appsettings if you ever set it there
            if (string.IsNullOrWhiteSpace(apiKey))
                apiKey = await _secrets.GetAsync("Deepgram:ApiKey", ct);

            if (string.IsNullOrWhiteSpace(apiKey))
                throw new InvalidOperationException("Deepgram ApiKey is not configured (Admin → Deepgram).");

            var uri = BuildUri();
            var ws = new ClientWebSocket();

            // Deepgram uses "Token" (not "Bearer")
            ws.Options.SetRequestHeader("Authorization", $"Token {apiKey}");
            ws.Options.SetRequestHeader("User-Agent", "AiInCourtAssistant/1.0 (+live)");
            ws.Options.KeepAliveInterval = TimeSpan.FromSeconds(15);

            await ws.ConnectAsync(uri, ct);
            if (ws.State != WebSocketState.Open)
                throw new WebSocketException($"Failed to connect to Deepgram. State={ws.State}");

            _log.LogInformation("Connected to Deepgram at {Uri} (model={Model}, interim={Interim})",
                uri, _opts.Model, _opts.InterimResults);

            return ws;
        }
    }
}
