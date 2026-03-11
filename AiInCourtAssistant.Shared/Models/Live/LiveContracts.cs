#nullable enable
using System.Collections.Generic;

namespace AiInCourtAssistant.Shared.Models.Live
{
    public enum LiveMessageKind
    {
        Info,
        Error,
        RowFocused,
        TranscriptPartial,
        TranscriptFinal,
        SuggestionsReady,
        SessionStarted,
        SessionStopped
    }

    public sealed record RowFocus(int EventId);

    public sealed record TranscriptUpdate(
        int EventId,
        string Text,
        bool IsFinal,
        double Confidence = 0d
    );

    public sealed record SuggestionsPayload(
    int EventId,
    IReadOnlyList<AiInCourtAssistant.Shared.Models.SuggestionDto> Suggestions
);

    public sealed record LiveEnvelope(
        LiveMessageKind Kind,
        object Payload
    )
    {
        public static LiveEnvelope RowFocused(int eventId) =>
            new(LiveMessageKind.RowFocused, new RowFocus(eventId));
        public static LiveEnvelope Partial(TranscriptUpdate u) =>
            new(LiveMessageKind.TranscriptPartial, u);
        public static LiveEnvelope Final(TranscriptUpdate u) =>
            new(LiveMessageKind.TranscriptFinal, u);
        public static LiveEnvelope Suggestions(SuggestionsPayload s) =>
            new(LiveMessageKind.SuggestionsReady, s);
    }

    public sealed record StartLiveSessionDto(int InitialEventId);
    public sealed record SwitchEventDto(int EventId);
    public sealed record StopLiveSessionDto();

    // client -> server audio
    public sealed record AudioChunkDto(byte[] Bytes, string Encoding, int SampleRate);
}
