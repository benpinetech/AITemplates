using AiInCourtAssistant.Server.Features.SessionTranscribe.Models;
using System.Text.RegularExpressions;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline
{
    public sealed class DemoSummarizer : ISummarizer
    {
        static readonly Regex RxNextDate = new(@"next court date (?:is|set (?:to|for))?\s+(?<when>[^\.]+)", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        static readonly Regex RxContinued = new(@"\b(matter|case)\b.*\bcontinued\b", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        static readonly Regex RxFTA = new(@"\b(fta\b|failed to appear)\b", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        static readonly Regex RxBWIssued = new(@"\bbench warrant\b.*\b(issued|considered)\b", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        static readonly Regex RxBWRemains = new(@"\bbench warrant remains in effect\b", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        static readonly Regex RxStatusReview = new(@"\bstatus review set\b", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        static readonly Regex RxGuilty = new(@"\b(entered a guilty plea|plea:\s*guilty)\b", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        static readonly Regex RxCompleted = new(@"\bcase (?:is )?completed\b", RegexOptions.IgnoreCase | RegexOptions.Compiled);

        public Task<string> SummarizeAsync(Guid tenantId, int eventId, IReadOnlyList<TranscriptSegment> segments, CancellationToken ct)
        {
            var text = string.Join(" ",
                segments.Where(s => !string.IsNullOrWhiteSpace(s.Text))
                        .Select(s => s.Text!.Trim()));

            // Most specific first
            if (RxGuilty.IsMatch(text) && RxCompleted.IsMatch(text))
                return Task.FromResult("Plea: guilty entered; case completed.");

            if (RxBWRemains.IsMatch(text))
                return Task.FromResult("Bench warrant remains; no further action.");

            if (RxFTA.IsMatch(text) && RxBWIssued.IsMatch(text))
                return Task.FromResult("FTA noted; bench warrant issued.");

            if (RxStatusReview.IsMatch(text))
            {
                var m = RxNextDate.Match(text);
                return Task.FromResult(m.Success
                    ? $"Status review set; next date {m.Groups["when"].Value}."
                    : "Status review set.");
            }

            if (RxContinued.IsMatch(text))
            {
                var m = RxNextDate.Match(text);
                return Task.FromResult(m.Success
                    ? $"Matter continued; next court date {m.Groups["when"].Value}."
                    : "Matter continued.");
            }

            // If only the date is present, surface it
            var onlyDate = RxNextDate.Match(text);
            if (onlyDate.Success)
                return Task.FromResult($"Next court date {onlyDate.Groups["when"].Value}.");

            // Safe fallback
            return Task.FromResult("Case reviewed on the record.");
        }
    }
}
