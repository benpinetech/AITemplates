namespace AiInCourtAssistant.Shared.Models;

public sealed class AudioChunkDto
{
    public int Sequence { get; set; }           // incremental frame number
    public long TimestampMs { get; set; }       // client-side capture time (ms)
    public short[] Pcm16 { get; set; } = default!;
}
