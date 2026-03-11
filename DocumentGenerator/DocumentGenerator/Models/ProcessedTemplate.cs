using System.Text;

namespace DocumentGenerator.Models;

public class ProcessedTemplate
{
    // expected value: the human-readable format of the text. If it is a formatted type, it will also contain the 
    // formatting values.
    public StringBuilder DecodedTemplateText { get; set; } = new StringBuilder();
    // expected value: a dictionary of the variable root name, and their value. A pair could look something like:
    // builtin | {"CaseID": 2, "Today": "2024-07-01 10:00:00"} or
    // cipList | [{"NameFirstName":"John","NameLastName":"Doe"}, {"NameLastName":"Doe", "NameFirstName": "Jane"}]
    public Dictionary<string, string> Variables { get; init; } = new(StringComparer.OrdinalIgnoreCase);
    public List<TemplateLogRow> LogRows { get; set; } = new();
    public bool UseResource { get; set; } = false;
    public int NestedLoopCount { get; set; } = 0;
}