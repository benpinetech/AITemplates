using DocumentGenerator.Utils;

namespace DocumentGenerator.Models;

public class TemplateLogRow
{
    public TemplateLogRow()
    {
        
    }
    public TemplateLogRow(string function, string message, string type)
    {
        Function = function;
        Message = message;
        Type = type;
    }

    public DateTime LogDateTime { get; set; } = DateTime.Now;
    public string Message { get; set; } = string.Empty;
    public string Function { get; set; } = string.Empty;
    public string Type { get; set; } = DocumentTemplateUtil.TemplateLogType.INFO;
}