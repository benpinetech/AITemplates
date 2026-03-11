using AiInCourtAssistant.Server.Features.SessionTranscribe.Models; // TranscriptSegment
using Microsoft.Extensions.Logging;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline
{
    public sealed class DeepgramBatchTranscriber : ITranscriptionProvider
    {
        private readonly DeepgramBatchClient _client;
        private readonly ILogger<DeepgramBatchTranscriber> _log;

        public DeepgramBatchTranscriber(DeepgramBatchClient client, ILogger<DeepgramBatchTranscriber> log)
        {
            _client = client;
            _log = log;
        }

        public bool SupportsDiarization => true;

        // Matches your interface: Task<IReadOnlyList<TranscriptSegment>> TranscribeAsync(string, CancellationToken)
        public async Task<IReadOnlyList<TranscriptSegment>> TranscribeAsync(string localFilePath, CancellationToken ct = default)
        {
            if (string.IsNullOrWhiteSpace(localFilePath) || !File.Exists(localFilePath))
                throw new FileNotFoundException("File not found.", localFilePath);

            var ext = Path.GetExtension(localFilePath);
            var contentType = GuessContentType(ext);

            await using var fs = File.OpenRead(localFilePath);
            var result = await _client.TranscribeAsync(fs, contentType, ct);

            var full = result.Transcript ?? string.Empty;
            _log.LogInformation("[DeepgramBatchTranscriber] Transcript length = {Len}", full.Length);
            // _log.LogInformation("[DeepgramBatchTranscriber] Transcript preview: {Preview}", full.Length > 160 ? full[..160] : full);

            // IMPORTANT: constructor is (speaker, startMs, endMs, text)
            var segment = new TranscriptSegment(
                null, // speaker
                0,    // startMs
                0,    // endMs
                full  // text
            );

            return new List<TranscriptSegment> { segment };
        }

        private static string GuessContentType(string? ext) => (ext ?? string.Empty).ToLowerInvariant() switch
        {
            // Audio
            ".wav" => "audio/wav",
            ".mp3" => "audio/mpeg",
            ".m4a" => "audio/mp4",
            ".aac" => "audio/aac",
            ".ogg" => "audio/ogg",
            ".oga" => "audio/ogg",
            ".opus" => "audio/ogg",
            ".amr" => "audio/amr",
            ".flac" => "audio/flac",
            // Video (Deepgram extracts audio)
            ".mp4" => "video/mp4",
            ".mov" => "video/quicktime",
            ".mkv" => "video/x-matroska",
            ".webm" => "video/webm",
            ".avi" => "video/x-msvideo",
            ".wmv" => "video/x-ms-wmv",
            _ => "application/octet-stream"
        };
    }
}
