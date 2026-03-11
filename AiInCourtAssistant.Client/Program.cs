using AiInCourtAssistant.Client;
using AiInCourtAssistant.Client.Services;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.AspNetCore.Components.WebAssembly.Hosting;

var builder = WebAssemblyHostBuilder.CreateDefault(args);

builder.RootComponents.Add<App>("#app");
builder.RootComponents.Add<HeadOutlet>("head::after");

// Single HttpClient for same-origin API calls ("/api/*")
builder.Services.AddScoped(sp => new HttpClient
{
    BaseAddress = new Uri(builder.HostEnvironment.BaseAddress)
});

// Client-side services only
builder.Services.AddScoped<LoginService>();
builder.Services.AddScoped<EventRepository>();
builder.Services.AddScoped<EventService>();
builder.Services.AddScoped<MediaFolderService>();
builder.Services.AddScoped<MediaUploader>();
builder.Services.AddScoped<AiInCourtAssistant.Client.Services.SessionTranscribeClient>();
builder.Services.AddScoped<AiRowClient>();
builder.Services.AddScoped<PineLinks>();
builder.Services.AddScoped<AiInCourtAssistant.Client.Services.TemplateMigrationClient>();


// ➕ Notes for Court API wrapper
builder.Services.AddScoped<CaseNoteClient>();              // allow [Inject] CaseNoteClient
builder.Services.AddScoped<ICaseNoteClient, CaseNoteClient>(); // if other code injects the interface

// If you actually use this elsewhere, keep it; otherwise remove to avoid confusion.
// builder.Services.AddScoped<MediaUploadService>();

await builder.Build().RunAsync();
