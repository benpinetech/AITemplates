using System.Threading;
using System.Threading.Tasks;

namespace AiInCourtAssistant.Client.Services
{
    public interface ICaseNoteClient
    {
        Task<string?> GetLatestAsync(int caseId, CancellationToken ct = default);
        Task<bool> SaveNfcAsync(int caseId, string text, CancellationToken ct = default);
    }
}
