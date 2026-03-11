using System.Text.RegularExpressions;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline
{
    public static class AutoAssign
    {
        // Accepts 24-CR-00123, 24 CR 00123, 24CR00123, etc.
        static readonly Regex CaseRx = new(@"\b\d{2}[-\s]?[A-Z]{1,3}[-\s]?\d{3,6}\b",
                                           RegexOptions.IgnoreCase | RegexOptions.Compiled);

        public static string? ExtractCaseNo(string? text)
        {
            if (string.IsNullOrWhiteSpace(text)) return null;
            var m = CaseRx.Match(text);
            return m.Success ? m.Value : null;
        }

        public static string Canon(string s) =>
            new string(s.Where(char.IsLetterOrDigit).ToArray()).ToUpperInvariant();
    }
}
