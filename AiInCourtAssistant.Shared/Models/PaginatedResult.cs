namespace AiInCourtAssistant.Shared.Models
{
    public class PaginatedResult<T>
    {
        public List<T> Items { get; set; } = new();
        public int RecordsInQuery { get; set; }
    }
}
