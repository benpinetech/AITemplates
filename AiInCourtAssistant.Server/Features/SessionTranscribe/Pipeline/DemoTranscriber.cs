using AiInCourtAssistant.Server.Features.SessionTranscribe.Models;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline;

public sealed class DemoTranscriber : ITranscriptionProvider
{
    public bool SupportsDiarization => true;

    public Task<IReadOnlyList<TranscriptSegment>> TranscribeAsync(string localFilePath, CancellationToken ct)
    {
        var segs = new List<TranscriptSegment>
        {
            new("JUDGE", 0, 6000, "Calling case 24-CR-00123 State v. Doe. Defendant failed to appear."),
            new("PROSECUTOR", 6000, 14000, "Request bench warrant and set next date October 7th."),
            new("JUDGE", 14000, 18000, "Bench warrant issued. Next date 10/07.")
        };
        return Task.FromResult((IReadOnlyList<TranscriptSegment>)segs);
    }
}
