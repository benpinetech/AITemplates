namespace DocumentGenerator.Models;

public class BuiltInTemplateFields
{
    public int? CaseID { get; set; }
    public DateTime Today { get; set; } = DateTime.Now;
    public int? CurrentUserUserID { get; set; }
    public int? CurrentUserPersonnelID { get; set; }
    public int? PaymentID { get; set; }
    public string FormattingType { get; set; }
}