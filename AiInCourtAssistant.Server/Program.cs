using AiInCourtAssistant.Server.Features.SessionTranscribe;
using AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline;
using AiInCourtAssistant.Server.Hubs;
using AiInCourtAssistant.Server.Services;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.Extensions.Options;
using Microsoft.IdentityModel.Tokens;
using System.IdentityModel.Tokens.Jwt;
using System.Net.Http.Headers;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;
using AiInCourtAssistant.Server.Features.Live;
using AiInCourtAssistant.Server.Features.Live.Deepgram;
using AiInCourtAssistant.Server.Features.Ai;
using AiInCourtAssistant.Server.Features.TemplateMigration;
using System.Text;

// Required for RtfPipe when RTF uses non-ASCII encodings (e.g. code pages)
Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);

var builder = WebApplication.CreateBuilder(args);

var upstreamBase = builder.Configuration["Upstream:BaseUrl"] ?? "https://sandbox.pinetech.com/";

var keysDir = Path.Combine(builder.Environment.ContentRootPath, "App_Data", "keys");
Directory.CreateDirectory(keysDir);

builder.Services.AddDataProtection()
    .PersistKeysToFileSystem(new DirectoryInfo(keysDir))
    .SetApplicationName("AiInCourtAssistant");

builder.Services.AddSingleton<ISecretStore, FileSecretStore>();
// REMOVED: OpenAI service (we’re deprecating OpenAI) 
// builder.Services.AddSingleton<IOpenAITranscriptionService, OpenAITranscriptionService>();
builder.Services.AddHttpContextAccessor();

// Fully qualify to ensure we use the SERVER EventService
builder.Services.AddScoped<
    AiInCourtAssistant.Server.Services.IEventService,
    AiInCourtAssistant.Server.Services.EventService>();

builder.Services.AddAuthorization(options =>
{
    // Policy that uses the passthrough scheme for proxy endpoints
    options.AddPolicy("AllowPineTokenPassthrough", policy =>
        policy.AddAuthenticationSchemes("PinePassthrough").RequireAuthenticatedUser());
});

builder.Services.AddAuthentication(JwtBearerDefaults.AuthenticationScheme)
    .AddJwtBearer(options =>
    {
        options.RequireHttpsMetadata = false;

        // Relaxed defaults (your current)
        options.TokenValidationParameters = new TokenValidationParameters
        {
            ValidateIssuer = false,
            ValidateAudience = false,
            ValidateLifetime = false,
            ValidateIssuerSigningKey = false
        };

        // DEV-ONLY: allow Pine JWT without local signature validation
        if (builder.Environment.IsDevelopment())
        {
            options.TokenValidationParameters.RequireSignedTokens = false;
            options.TokenValidationParameters.SignatureValidator = (token, parameters) =>
                new JwtSecurityToken(token);
        }

        options.Events = new JwtBearerEvents
        {
            OnMessageReceived = context =>
            {
                var accessToken = context.Request.Query["access_token"];
                var path = context.HttpContext.Request.Path;

                // Accept tokens on hub + legacy paths
                if (!string.IsNullOrEmpty(accessToken) &&
                    (path.StartsWithSegments("/hubs/live-transcribe")
                     || path.StartsWithSegments("/live-transcribe")
                     || path.StartsWithSegments("/hubs/live")))
                {
                    context.Token = accessToken;
                }
                return Task.CompletedTask;
            }
        };
    })
    // NEW: passthrough scheme used ONLY by proxy controllers via policy
    .AddScheme<AuthenticationSchemeOptions, PinePassthroughHandler>("PinePassthrough", _ => { });

builder.Services.AddHttpClient();
builder.Services.AddTransient<ForwardBearerHandler>();

// Bind Deepgram + LiveTranscribe options from configuration
builder.Services.Configure<DeepgramOptions>(builder.Configuration.GetSection("Deepgram"));
builder.Services.Configure<LiveTranscribeOptions>(builder.Configuration.GetSection("LiveTranscribe"));
builder.Services.AddScoped<IRowAiService, RowAiService>();
builder.Services.AddScoped<RtfToHtmlService>();
builder.Services.AddScoped<MigrationService>();
builder.Services.AddScoped<TemplateGenerateService>();

/* ──────────────────────────────────────────────────────────────────────────────
   Live Transcribe: ALWAYS register Deepgram realtime (no config gate)
   ──────────────────────────────────────────────────────────────────────────── */
builder.Services.AddSingleton<IDeepgramStreamFactory, DeepgramStreamFactory>();
builder.Services.AddSingleton<DeepgramLiveEngine>();
builder.Services.AddSingleton<ILiveStreamEngine>(sp => sp.GetRequiredService<DeepgramLiveEngine>());
builder.Services.AddSingleton<ILiveSessionManager, LiveSessionManager>();

builder.Services.AddHttpClient("AuthorizedClient", client =>
{
    client.BaseAddress = new Uri(upstreamBase);
    client.Timeout = TimeSpan.FromSeconds(100);
})
.ConfigurePrimaryHttpMessageHandler(() => new HttpClientHandler { AllowAutoRedirect = false })
.AddHttpMessageHandler<ForwardBearerHandler>();

builder.Services
    .AddControllers()
    .AddJsonOptions(o =>
    {
        o.JsonSerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.CamelCase;
        o.JsonSerializerOptions.DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull;
    });

builder.Services.AddRazorPages();

//
// Named client for Pine proxy with bearer forwarding (header or cookie)
// and redirects disabled so Pine login pages don't come back as HTML.
//
builder.Services.AddHttpClient("PineProxy", (sp, client) =>
{
    var cfg = sp.GetRequiredService<IConfiguration>();
    var baseUrl = cfg["PineApi:BaseUrl"];
    if (string.IsNullOrWhiteSpace(baseUrl))
        throw new InvalidOperationException("Missing config: PineApi:BaseUrl");

    client.BaseAddress = new Uri(baseUrl, UriKind.Absolute);
    client.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
})
.ConfigurePrimaryHttpMessageHandler(() => new HttpClientHandler { AllowAutoRedirect = false })
.AddHttpMessageHandler<ForwardBearerHandler>();

// SignalR
// SignalR + MessagePack
builder.Services
    .AddSignalR(o =>
    {
        o.EnableDetailedErrors = true;
        o.MaximumReceiveMessageSize = 8 * 1024 * 1024;
        o.KeepAliveInterval = TimeSpan.FromSeconds(15);
        o.ClientTimeoutInterval = TimeSpan.FromSeconds(60);
    })
    .AddMessagePackProtocol(); // MessagePack protocol for binary payloads

/* ──────────────────────────────────────────────────────────────────────────────
   Session Transcription feature wiring (uploads/preview)
   ──────────────────────────────────────────────────────────────────────────── */
var featureSession = builder.Configuration.GetValue<bool>("Features:SessionTranscription");
if (featureSession)
{
    // Storage for staged sessions/chunks
    builder.Services.AddSingleton<
        AiInCourtAssistant.Server.Features.SessionTranscribe.Staging.ISessionTranscribeRepo,
        AiInCourtAssistant.Server.Features.SessionTranscribe.Staging.FileSessionTranscribeRepo>();

    // In-memory queue used by the worker
    builder.Services.AddSingleton<
        AiInCourtAssistant.Server.Features.SessionTranscribe.Queue.ISessionTranscribeQueue,
        AiInCourtAssistant.Server.Features.SessionTranscribe.Queue.InMemorySessionTranscribeQueue>();

    // Deepgram batch client (uploads/preview) and pipeline transcriber
    builder.Services.AddHttpClient<AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline.DeepgramBatchClient>();
    builder.Services.AddSingleton<
        AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline.ITranscriptionProvider,
        AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline.DeepgramBatchTranscriber>();

    // ✅ SUMMARIZER
    builder.Services.AddHttpClient<StructuredAiSummarizer>();
    builder.Services.AddScoped<ISummarizer, StructuredAiSummarizer>();

    // 💡 SUGGESTION ENGINE (used by both upload + live)
    builder.Services.AddSingleton<ISuggestionEngine, RuleSuggestionEngine>();

    // Session context cache
    builder.Services.AddSingleton<
        AiInCourtAssistant.Server.Features.SessionTranscribe.SessionContextStore>();

    // Background processor
    builder.Services.AddHostedService<
        AiInCourtAssistant.Server.Features.SessionTranscribe.SessionTranscribeWorker>();
}

var app = builder.Build();

app.MapGet("/_diag/static", (IWebHostEnvironment env) =>
{
    var root = env.WebRootPath ?? "(null)";
    var files = Directory.Exists(root)
        ? Directory.GetFiles(root).Select(Path.GetFileName)
        : Array.Empty<string>();
    return Results.Json(new { webRoot = root, files });
});

if (!app.Environment.IsDevelopment())
{
    app.UseExceptionHandler("/Error");
    app.UseHsts();
}

app.UseHttpsRedirection();

// Optional hard interceptor for live-listen.js
app.Use(async (ctx, next) =>
{
    if (ctx.Request.Path.Equals("/live-listen.js", StringComparison.OrdinalIgnoreCase))
    {
        var env = ctx.RequestServices.GetRequiredService<IWebHostEnvironment>();
        var full = Path.Combine(env.WebRootPath ?? "", "live-listen.js");
        if (!System.IO.File.Exists(full))
        {
            ctx.Response.StatusCode = StatusCodes.Status404NotFound;
            await ctx.Response.WriteAsync("// live-listen.js not found in Server/wwwroot");
            return;
        }
        ctx.Response.ContentType = "application/javascript; charset=utf-8";
        await ctx.Response.SendFileAsync(full);
        return;
    }
    await next();
});

app.UseBlazorFrameworkFiles();
app.UseStaticFiles();

app.UseRouting();

app.UseAuthentication();
app.UseAuthorization();

app.MapControllers();

// after app.UseRouting();
app.MapHub<LiveTranscribeHub>("/hubs/live-transcribe");

app.MapRazorPages();

// SPA fallback LAST
app.MapFallbackToFile("index.html");

app.Run();

/// <summary>
/// Forwards Bearer from Authorization header OR from pine_token cookie to upstream.
/// </summary>
public sealed class ForwardBearerHandler : DelegatingHandler
{
    private readonly IHttpContextAccessor _http;
    public ForwardBearerHandler(IHttpContextAccessor http) => _http = http;

    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        if (request.Headers.Authorization is null)
        {
            var authHeader = _http.HttpContext?.Request?.Headers["Authorization"].ToString();

            // Fallback to pine_token cookie if no Authorization header is present
            if (string.IsNullOrWhiteSpace(authHeader))
            {
                if (_http.HttpContext?.Request?.Cookies.TryGetValue("pine_token", out var cookieToken) == true &&
                    !string.IsNullOrWhiteSpace(cookieToken))
                {
                    request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", cookieToken);
                    return base.SendAsync(request, cancellationToken);
                }
            }

            if (!string.IsNullOrWhiteSpace(authHeader))
            {
                if (authHeader.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
                    authHeader = authHeader.Substring("Bearer ".Length).Trim();

                if (!string.IsNullOrWhiteSpace(authHeader))
                    request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", authHeader);
            }
        }
        return base.SendAsync(request, cancellationToken);
    }
}

/// <summary>
/// DEV passthrough auth handler: treat the request as authenticated if we see
/// (1) Authorization: Bearer xxx, OR (2) pine_token cookie, OR (3) access_token in query.
/// Used ONLY on proxy controllers via the "AllowPineTokenPassthrough" policy.
/// </summary>
public sealed class PinePassthroughHandler : AuthenticationHandler<AuthenticationSchemeOptions>
{
    public PinePassthroughHandler(
        IOptionsMonitor<AuthenticationSchemeOptions> options,
        ILoggerFactory logger,
        UrlEncoder encoder,
        ISystemClock clock) : base(options, logger, encoder, clock) { }

    protected override Task<AuthenticateResult> HandleAuthenticateAsync()
    {
        // 1) Authorization: Bearer ...
        var auth = Request.Headers["Authorization"].ToString();
        if (!string.IsNullOrWhiteSpace(auth) && auth.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
            return Success();

        // 2) pine_token cookie (same-origin browser calls)
        if (Request.Cookies.TryGetValue("pine_token", out var cookieToken) && !string.IsNullOrWhiteSpace(cookieToken))
            return Success();

        // 3) access_token query (mirrors SignalR pattern)
        var accessToken = Request.Query["access_token"].ToString();
        if (!string.IsNullOrWhiteSpace(accessToken))
            return Success();

        return Task.FromResult(AuthenticateResult.NoResult());

        Task<AuthenticateResult> Success()
        {
            var identity = new System.Security.Claims.ClaimsIdentity("PinePassthrough");
            var principal = new System.Security.Claims.ClaimsPrincipal(identity);
            var ticket = new AuthenticationTicket(principal, "PinePassthrough");
            return Task.FromResult(AuthenticateResult.Success(ticket));
        }
    }
}
