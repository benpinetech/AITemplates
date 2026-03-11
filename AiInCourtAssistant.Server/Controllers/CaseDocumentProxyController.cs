using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace AiInCourtAssistant.Server.Controllers;

[ApiController]
[Route("api/proxy/caseDocument")]
[Authorize(Policy = "AllowPineTokenPassthrough")]
public sealed class CaseDocumentProxyController : ControllerBase
{
    private readonly HttpClient _http;

    public CaseDocumentProxyController(IHttpClientFactory factory)
    {
        _http = factory.CreateClient("PineProxy");
    }

    /// <summary>Preview document for a case. Proxies GET api/caseDocument/preview/{caseId}.</summary>
    [HttpGet("preview/{caseId:int}")]
    public async Task<IActionResult> Preview([FromRoute] int caseId, CancellationToken ct)
    {
        try
        {
            var resp = await _http.GetAsync($"api/caseDocument/preview/{caseId}", HttpCompletionOption.ResponseHeadersRead, ct);
            var contentType = resp.Content.Headers.ContentType?.MediaType ?? "application/octet-stream";
            var bytes = await resp.Content.ReadAsByteArrayAsync(ct);
            if (!resp.IsSuccessStatusCode)
                return StatusCode((int)resp.StatusCode, new { error = "Upstream error", status = (int)resp.StatusCode, bodyPreview = bytes.Length > 200 ? null : System.Text.Encoding.UTF8.GetString(bytes) });
            return new FileContentResult(bytes, contentType) { FileDownloadName = (string?)null };
        }
        catch (Exception ex)
        {
            return StatusCode(502, new { error = "ProxyRequestFailed", message = ex.Message });
        }
    }
}
