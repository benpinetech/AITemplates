using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace AiInCourtAssistant.Server.Controllers;

[ApiController]
[Route("api/proxy/template")]
[Authorize(Policy = "AllowPineTokenPassthrough")]
public sealed class TemplateProxyController : ControllerBase
{
    private readonly HttpClient _http;

    public TemplateProxyController(IHttpClientFactory factory)
    {
        _http = factory.CreateClient("PineProxy");
    }

    /// <summary>Create a new Pine template. Forwards to Pine API (e.g. POST api/template or api/caseDocument/template).</summary>
    [HttpPost]
    public async Task<IActionResult> Create([FromBody] object body, CancellationToken ct)
    {
        try
        {
            var resp = await _http.PostAsJsonAsync("api/template", body, ct);
            var content = await resp.Content.ReadAsStringAsync(ct);
            return new ContentResult
            {
                StatusCode = (int)resp.StatusCode,
                Content = content,
                ContentType = resp.Content.Headers.ContentType?.ToString() ?? "application/json"
            };
        }
        catch (Exception ex)
        {
            return StatusCode(502, new { error = "ProxyRequestFailed", message = ex.Message });
        }
    }

    /// <summary>Update an existing Pine template.</summary>
    [HttpPut("{id}")]
    public async Task<IActionResult> Update([FromRoute] string id, [FromBody] object body, CancellationToken ct)
    {
        try
        {
            var resp = await _http.PutAsJsonAsync($"api/template/{id}", body, ct);
            var content = await resp.Content.ReadAsStringAsync(ct);
            return new ContentResult
            {
                StatusCode = (int)resp.StatusCode,
                Content = content,
                ContentType = resp.Content.Headers.ContentType?.ToString() ?? "application/json"
            };
        }
        catch (Exception ex)
        {
            return StatusCode(502, new { error = "ProxyRequestFailed", message = ex.Message });
        }
    }
}
