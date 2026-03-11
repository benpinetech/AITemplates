namespace AiInCourtAssistant.Client.Services;

public sealed class PineLinks
{
    private readonly IConfiguration _cfg;
    public PineLinks(IConfiguration cfg) => _cfg = cfg;

    public string CaseNotes(int caseId)
    {
        var baseUrl = (_cfg["PineUi:BaseUrl"] ?? "https://sandbox.pinetech.com").TrimEnd('/');

        return $"{baseUrl}/case/{caseId}?Page=Note&ParentTable=CaseNote";
    }

    public string CaseHome(int caseId)
    {
        var baseUrl = (_cfg["PineUi:BaseUrl"] ?? "https://sandbox.pinetech.com").TrimEnd('/');
        return $"{baseUrl}/case/{caseId}";
    }
}
