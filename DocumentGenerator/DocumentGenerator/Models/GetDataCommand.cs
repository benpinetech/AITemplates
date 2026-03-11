namespace DocumentGenerator.Models;

public class GetDataCommand
{
    public string Entity { get; set; }
    public string Method { get; set; }
    public int? RootID { get; set; }
    public Dictionary<string, string> Filters { get; set; } = new();
}