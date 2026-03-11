namespace AiInCourtAssistant.Shared.Models;

public sealed class StartSessionDto
{
    public string Courtroom { get; set; } = "";
    public DateTime DocketDate { get; set; } = DateTime.UtcNow.Date;
    public int? ActiveEventId { get; set; }
}
