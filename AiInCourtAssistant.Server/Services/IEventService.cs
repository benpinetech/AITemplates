using AiInCourtAssistant.Shared.Models;

namespace AiInCourtAssistant.Server.Services
{
    public interface IEventService
    {
        Task<AdvancedEventSearchResponse?> SearchAsync(
            AdvancedEventSearch request,
            CancellationToken ct = default);

        Task<bool> UpdateEventAsync(
            EventUpdateDto update,
            CancellationToken ct = default);
        Task UploadTranscriptAsync(int eventId, string fileName, string content, CancellationToken ct);


        Task<int> SaveBulkNotesAsync(
            IEnumerable<NoteUpdateDto> items,
            CancellationToken ct = default);
        Task<bool> UpdateCaseStatusAsync(int caseId, string newStatus, CancellationToken ct = default);
    }
}
