using AiInCourtAssistant.Shared.Models;

namespace AiInCourtAssistant.Server.Features.Ai
{
    public interface IRowAiService
    {
        Task<AiRowResponse> SummarizeRowTextAsync(string text, CancellationToken ct);
    }
}
