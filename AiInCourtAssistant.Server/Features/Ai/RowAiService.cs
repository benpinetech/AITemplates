using AiInCourtAssistant.Server.Features.SessionTranscribe; // ISummarizer
using AiInCourtAssistant.Server.Features.SessionTranscribe.Pipeline;
using AiInCourtAssistant.Shared.Models;
using Microsoft.Extensions.Logging;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using TranscriptSegment = AiInCourtAssistant.Server.Features.SessionTranscribe.Models.TranscriptSegment;

namespace AiInCourtAssistant.Server.Features.Ai
{
    public sealed class RowAiService : IRowAiService
    {
        private readonly ISummarizer _summarizer;
        private readonly ISuggestionEngine _suggestions; // kept for DI compatibility
        private readonly ILogger<RowAiService> _log;

        public RowAiService(
            ISummarizer summarizer,
            ISuggestionEngine suggestions,
            ILogger<RowAiService> log)
        {
            _summarizer = summarizer;
            _suggestions = suggestions;
            _log = log;
        }

        private static bool ShouldSummarize(string text)
        {
            if (string.IsNullOrWhiteSpace(text))
                return false;

            var lower = text.ToLowerInvariant();

            if (Regex.IsMatch(
                lower,
                @"failure to appear|failed to appear|\bfta\b|bench warrant|" +
                @"plead(ed)?\s+guilty|plea[, ]+guilty|no contest|nolo contendere|" +
                @"next court (date|hearing)|next hearing|review date|status review date|" +
                @"fine|court costs?",
                RegexOptions.IgnoreCase))
            {
                return true;
            }

            return text.Length >= 40;
        }

        public async Task<AiRowResponse> SummarizeRowTextAsync(string text, CancellationToken ct)
        {
            text ??= string.Empty;

            var preview = text.Length <= 200 ? text : text.Substring(0, 200);
            _log.LogInformation("RowAI: SummarizeRowTextAsync len={Len}, preview=\"{Preview}\"",
                text.Length, preview);

            if (!ShouldSummarize(text))
            {
                return new AiRowResponse(
                    Summary: string.Empty,
                    Suggestions: Array.Empty<SuggestionDto>(),
                    Confidence: 0.0);
            }

            var narrative = await GetNarrativeAsync(text, ct);
            var summary = BuildStructuredRuleSummary(text, narrative);

            var suggestions = new List<SuggestionDto>();
            AddRuleBasedSuggestions(suggestions, text);

            if (suggestions.Count == 0)
            {
                suggestions.Add(new SuggestionDto(
                    Title: "Review and Record Hearing Outcome",
                    Detail: "No specific rule-based trigger was detected in this transcript.",
                    Body: "Review the transcript, record the plea or disposition in case notes, and confirm that any fines, dates, or conditions mentioned are updated in the case and calendar.",
                    Confidence: 0.50
                ));
            }

            var confidence = suggestions.Count > 0 ? 0.8 : 0.6;

            _log.LogDebug(
                "RowAI produced summary length {SummaryLen} and {SuggestionCount} suggestions.",
                summary?.Length ?? 0,
                suggestions.Count);

            return new AiRowResponse(summary, suggestions, Confidence: confidence);
        }

        private async Task<string> GetNarrativeAsync(string text, CancellationToken ct)
        {
            var fallback = FallbackFromText(text);
            if (string.IsNullOrWhiteSpace(text))
                return fallback;

            try
            {
                var seg = new TranscriptSegment(null, 0, text.Length, text);

                // Make collisions extremely unlikely
                var eventKey = RandomNumberGenerator.GetInt32(int.MinValue, int.MaxValue);

                var raw = await _summarizer.SummarizeAsync(
                    tenantId: Guid.Empty,
                    eventId: eventKey,
                    segments: new[] { seg },
                    ct: ct);

                if (string.IsNullOrWhiteSpace(raw))
                    return fallback;

                return raw;
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "RowAI: LLM narrative failed; falling back.");
                return fallback;
            }
        }

        private static string BuildStructuredRuleSummary(string t, string narrativeOverride)
        {
            t ??= string.Empty;

            string plea = "-";
            string disposition = "-";
            string fineFees = "-";
            string nextDate = "-";
            string conditions = "-";
            string actionItems = "-";

            var lower = t.ToLowerInvariant();

            if (Regex.IsMatch(lower, @"plead(ed)?\s+guilty|plea[, ]+guilty", RegexOptions.IgnoreCase))
                plea = "Guilty";
            else if (Regex.IsMatch(lower,
                @"plea[, ]+not guilty|plead(ed)?\s+not guilty|enter(ed)?\s+a\s+plea\s+of\s+not guilty|\bnot guilty\b",
                RegexOptions.IgnoreCase))
                plea = "Not guilty";
            else if (Regex.IsMatch(lower, @"no contest|nolo contendere", RegexOptions.IgnoreCase))
                plea = "No contest";
            else if (Regex.IsMatch(lower, @"diversion offer|accept diversion", RegexOptions.IgnoreCase))
                plea = "Accepted diversion";

            if (Regex.IsMatch(lower, @"court accepts? the plea|plea accepted", RegexOptions.IgnoreCase))
                disposition = "Accepted plea";
            else if (Regex.IsMatch(lower, @"dismiss(ed|al)|case dismissed|charge dismissed", RegexOptions.IgnoreCase))
                disposition = "Case dismissed";
            else if (Regex.IsMatch(lower, @"continued for|continued to|status is continued|matter continued", RegexOptions.IgnoreCase))
                disposition = "Continued / set for review";
            else if (Regex.IsMatch(lower, @"failed to appear|failure to appear|fta", RegexOptions.IgnoreCase))
                disposition = "Failure to appear";
            else if (Regex.IsMatch(lower, @"event status completed|events status completed", RegexOptions.IgnoreCase))
                disposition = "Completed";

            var fineMatch = Regex.Match(t, @"(?:fine|fined)[^\d]{0,40}(\d{1,5})", RegexOptions.IgnoreCase);
            var costMatch = Regex.Match(t, @"(?:costs?|court costs?)[^\d]{0,40}(\d{1,5})", RegexOptions.IgnoreCase);

            if (fineMatch.Success || costMatch.Success)
            {
                var fineAmt = fineMatch.Success ? fineMatch.Groups[1].Value : null;
                var costAmt = costMatch.Success ? costMatch.Groups[1].Value : null;

                if (fineAmt != null && costAmt != null)
                    fineFees = $"${fineAmt} fine + ${costAmt} court costs";
                else if (fineAmt != null)
                    fineFees = $"${fineAmt} fine";
                else if (costAmt != null)
                    fineFees = $"${costAmt} court costs";
            }

            var condParts = new List<string>();
            if (Regex.IsMatch(lower, @"no jail", RegexOptions.IgnoreCase))
                condParts.Add("No jail");
            if (Regex.IsMatch(lower, @"traffic school|school option", RegexOptions.IgnoreCase))
                condParts.Add("Traffic school option");
            if (Regex.IsMatch(lower, @"proof of (?:insurance|correction|renewal|policy)", RegexOptions.IgnoreCase))
                condParts.Add("Proof required (insurance/correction/renewal)");
            if (Regex.IsMatch(lower, @"completion avoids points", RegexOptions.IgnoreCase))
                condParts.Add("Completion avoids points");

            // Show “Bond terms stated” only if bond/bail appears
            if (Regex.IsMatch(lower, @"\bbond\b|\bbail\b", RegexOptions.IgnoreCase))
                condParts.Add("Bond terms stated");

            if (condParts.Count > 0)
                conditions = string.Join("; ", condParts);

            var hasNextDateLanguage =
    lower.Contains("next court date") ||
    lower.Contains("review date") ||
    lower.Contains("status review date") ||
    lower.Contains("pretrial") ||
    lower.Contains("pretrial conference") ||
    lower.Contains("continued to") ||
    lower.Contains("continued for") ||
    Regex.IsMatch(lower, @"\b\d{1,2}/\d{1,2}/\d{4}\b");

            if (hasNextDateLanguage)
            {
                // Numeric dates like:
                // 02/08/2026
                // 02/08/2026 at 10:30AM
                // 02/08/2026 9 AM
                var numericDate = Regex.Match(t,
                    @"\b(\d{1,2}/\d{1,2}/\d{4})\b(?:\s*(?:at)?\s*([0-9]{1,2}(?::[0-9]{2})?\s*(?:AM|PM)))?",
                    RegexOptions.IgnoreCase);

                if (numericDate.Success)
                {
                    var date = numericDate.Groups[1].Value;
                    var time = numericDate.Groups[2].Success
                        ? numericDate.Groups[2].Value.Replace(" ", "")
                        : "";

                    nextDate = string.IsNullOrWhiteSpace(time)
                        ? date
                        : $"{date} {time}";
                }
                else
                {
                    // Long dates like:
                    // January 10, 2026 at 9 AM
                    var longDate = Regex.Match(t,
                        @"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b(?:\s*(?:at)?\s*([0-9]{1,2}(?::[0-9]{2})?\s*(?:AM|PM)))?",
                        RegexOptions.IgnoreCase);

                    if (longDate.Success)
                    {
                        var dateText = longDate.Groups[0].Value.Trim();
                        var time = longDate.Groups[2].Success
                            ? longDate.Groups[2].Value.Replace(" ", "")
                            : "";

                        nextDate = string.IsNullOrWhiteSpace(time)
                            ? dateText
                            : $"{dateText} {time}";
                    }
                }
            }
            if (string.IsNullOrWhiteSpace(nextDate))
                nextDate = "-";

            if (Regex.IsMatch(lower, @"bench warrant", RegexOptions.IgnoreCase))
                actionItems = "Confirm bench warrant issued and update status.";
            else if (Regex.IsMatch(lower, @"case dismissed", RegexOptions.IgnoreCase))
                actionItems = "Update case/event status to dismissed/complete.";
            else if (Regex.IsMatch(lower, @"status is continued|continued for|continued to", RegexOptions.IgnoreCase))
                actionItems = "Update next court date/time and set event status to Continued.";
            else if (Regex.IsMatch(lower, @"payment.*due|due in|payment plan|monthly payments|installments?", RegexOptions.IgnoreCase))
                actionItems = "Confirm payment due date/plan and update financials.";

            string narrative = !string.IsNullOrWhiteSpace(narrativeOverride)
                ? narrativeOverride
                : FallbackFromText(t);

            var trimmed = narrative.TrimStart();
            if (trimmed.StartsWith("1) ")) narrative = trimmed.Substring(3).TrimStart();
            else if (trimmed.StartsWith("1. ")) narrative = trimmed.Substring(3).TrimStart();

            var sb = new StringBuilder();
            sb.AppendLine("AI Summary");
            sb.AppendLine();
            sb.Append("1) ");
            sb.AppendLine(narrative);
            sb.AppendLine();
            sb.AppendLine("2)");
            sb.AppendLine($"- Plea: {plea}");
            sb.AppendLine($"- Finding/Disposition: {disposition}");
            sb.AppendLine($"- Fine/Fees: {fineFees}");
            sb.AppendLine($"- Next Date: {nextDate}");
            sb.AppendLine($"- Conditions: {conditions}");
            sb.AppendLine($"- Action/Items: {actionItems}");

            var summary = sb.ToString();

            // Safety net: prevent accidental duplicated "2)"
            var first2 = summary.IndexOf("\n2)", StringComparison.Ordinal);
            if (first2 >= 0)
            {
                var second2 = summary.IndexOf("\n2)", first2 + 3, StringComparison.Ordinal);
                if (second2 > first2)
                    summary = summary.Substring(0, second2).TrimEnd();
            }

            return summary;
        }

        private static string FallbackFromText(string t)
        {
            t ??= "";
            var parts = Regex.Split(t, @"(?<=[\.\!\?])\s+")
                             .Where(x => !string.IsNullOrWhiteSpace(x))
                             .Take(2);
            var s = string.Join(" ", parts);
            return s.Length > 400 ? s[..400] + "…" : (string.IsNullOrWhiteSpace(s) ? "No substantive transcript yet." : s);
        }

        private static void AddRuleBasedSuggestions(List<SuggestionDto> dest, string text)
        {
            if (string.IsNullOrWhiteSpace(text)) return;

            var t = text;
            var lower = t.ToLowerInvariant();

            var isFta =
                Regex.IsMatch(lower, @"\b(fta|failure to appear|failed to appear|non[- ]appearance)\b", RegexOptions.IgnoreCase) ||
                Regex.IsMatch(lower, @"\b(defendant|she|he)\s+(was|is|remains)\s+(not present|absent)\b", RegexOptions.IgnoreCase);

            if (isFta)
            {
                dest.Add(new SuggestionDto(
                    Title: "Update event status to FTA",
                    Detail: "FTA / nonappearance language detected.",
                    Body: "Verify attendance and prior notice. If FTA is confirmed under local rules, update the event status to FTA and ensure the docket reflects the nonappearance.",
                    Confidence: 0.85));
            }

            if (Regex.IsMatch(lower, @"bench warrant|warrant for (her|his) arrest", RegexOptions.IgnoreCase))
            {
                dest.Add(new SuggestionDto(
                    Title: "Update case status to Bench Warrant",
                    Detail: "Bench-warrant language detected.",
                    Body: "Confirm that the warrant has been entered in the case management system, transmitted as required, and that the case status reflects the bench warrant.",
                    Confidence: 0.80));
            }

            // --- Bond / bail amount (supports: "bond $500" AND "$500 bond")
            var bondMatch1 = Regex.Match(t, @"\b(?:bond|bail)\b[^\d]{0,40}(\d{1,6})", RegexOptions.IgnoreCase);
            var bondMatch2 = Regex.Match(t,
                @"\b\$(\d{1,6})\b[^\w]{0,10}\b(?:bond|bail)\b|\b(\d{1,6})\b[^\w]{0,10}\b(?:bond|bail)\b",
                RegexOptions.IgnoreCase);

            string? bondAmt = null;
            if (bondMatch1.Success) bondAmt = bondMatch1.Groups[1].Value;
            else if (bondMatch2.Success) bondAmt = bondMatch2.Groups[1].Success ? bondMatch2.Groups[1].Value : bondMatch2.Groups[2].Value;

            if (!string.IsNullOrWhiteSpace(bondAmt))
            {
                dest.Add(new SuggestionDto(
                    Title: $"Set bond amount to ${bondAmt}",
                    Detail: "Bond / bail amount detected in transcript.",
                    Body: $"Set the bond amount to ${bondAmt} on the case/event, consistent with the order stated on the record.",
                    Confidence: 0.78));
            }

            if (Regex.IsMatch(lower, @"proof of insurance|proof of correction|proof of renewal|bring proof of", RegexOptions.IgnoreCase))
            {
                dest.Add(new SuggestionDto(
                    Title: "Set a condition to bring proof of insurance",
                    Detail: "Transcript references a proof or compliance requirement.",
                    Body: "Add a condition requiring the defendant to bring the specified proof (insurance, correction, renewal, etc.) by the review date, and track that requirement on the calendar and in case notes.",
                    Confidence: 0.80));
            }

            var csMatch = Regex.Match(t, @"(\d{1,3})\s+hours?\s+of\s+community\s+service", RegexOptions.IgnoreCase);
            if (csMatch.Success)
            {
                var hours = csMatch.Groups[1].Value;
                dest.Add(new SuggestionDto(
                    Title: $"Add a condition for {hours} hours of community service",
                    Detail: "Community-service requirement detected.",
                    Body: $"Add a condition requiring the defendant to complete {hours} hours of community service, linked to this event/case.",
                    Confidence: 0.78));
            }

            if (Regex.IsMatch(lower, @"traffic school|driving school|defensive driving school", RegexOptions.IgnoreCase))
            {
                dest.Add(new SuggestionDto(
                    Title: "Add a condition for traffic school",
                    Detail: "Traffic-school option or requirement detected.",
                    Body: "Add a condition requiring the defendant to complete the specified traffic-school program and link completion to the case outcome.",
                    Confidence: 0.78));
            }

            if (Regex.IsMatch(lower,
    @"\bcontinuance\b|\bcontinued\b|\bcontinued for\b|\bcontinued to\b|\bnext court date\b|\bnext court hearing\b|\breview date\b|\bstatus review\b|\bpretrial\b|\bpretrial conference\b|\bpre[- ]?trial\b",
    RegexOptions.IgnoreCase))
            {
                dest.Add(new SuggestionDto(
                    Title: "Update event status to Continued",
                    Detail: "Continuance/review/pretrial language detected.",
                    Body: "Set the event status to Continued and make sure the calendar reflects any new review/status/pretrial dates.",
                    Confidence: 0.75));

                string dateTimeText = string.Empty;

                // numeric date like 02/08/2026 at 10:30AM OR 02/08/2026 10:30 AM OR 02/08/2026
                var numeric = Regex.Match(t,
                    @"\b(\d{1,2}/\d{1,2}/\d{4})\b(?:\s*(?:at)?\s*([0-9]{1,2}(?::[0-9]{2})?\s*(?:AM|PM)))?",
                    RegexOptions.IgnoreCase);

                if (numeric.Success)
                {
                    var date = numeric.Groups[1].Value;
                    var time = numeric.Groups[2].Success ? numeric.Groups[2].Value.Replace(" ", "") : "";
                    dateTimeText = string.IsNullOrWhiteSpace(time) ? date : $"{date} {time}";
                }
                else
                {
                    // long date like January 10, 2026 at 9 AM (or "January 10 2026 9:00AM")
                    var longDate = Regex.Match(t,
                        @"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b(?:\s*(?:at)?\s*([0-9]{1,2}(?::[0-9]{2})?\s*(?:AM|PM)))?",
                        RegexOptions.IgnoreCase);

                    if (longDate.Success)
                    {
                        var d = longDate.Groups[0].Value.Trim();
                        var time = longDate.Groups[2].Success ? longDate.Groups[2].Value.Replace(" ", "") : "";
                        dateTimeText = string.IsNullOrWhiteSpace(time) ? d : $"{d} {time}";
                    }
                }

                if (!string.IsNullOrWhiteSpace(dateTimeText))
                {
                    string label = "event";

                    if (Regex.IsMatch(lower, @"status review", RegexOptions.IgnoreCase))
                        label = "status-review event";
                    else if (Regex.IsMatch(lower, @"review hearing", RegexOptions.IgnoreCase))
                        label = "review hearing";
                    else if (Regex.IsMatch(lower, @"pretrial conference|pretrial|pre[- ]?trial", RegexOptions.IgnoreCase))
                        label = "pretrial conference";
                    else if (Regex.IsMatch(lower, @"arraignment", RegexOptions.IgnoreCase))
                        label = "arraignment event";

                    dest.Add(new SuggestionDto(
                        Title: $"Add {label} for {dateTimeText}",
                        Detail: "Future court date/time detected.",
                        Body: $"Create a {label} on the calendar for {dateTimeText}, matching the date and time stated on the record.",
                        Confidence: 0.72));
                }
            }


            if (Regex.IsMatch(lower, @"\bplead(?:s|ed|ing)?\s+guilty\b|\bplea[, ]+guilty\b", RegexOptions.IgnoreCase))
            {
                dest.Add(new SuggestionDto(
                    Title: "Set plea to Guilty",
                    Detail: "Guilty-plea language detected.",
                    Body: "Record the plea as Guilty for the relevant counts and link it to this event.",
                    Confidence: 0.78));
            }
            else if (Regex.IsMatch(lower,
                @"\bplea[, ]+not guilty\b|\bplead(?:s|ed|ing)?\s+not guilty\b|\benter(?:s|ed)?\s+a\s+plea\s+of\s+not guilty\b|\bnot guilty\b",
                RegexOptions.IgnoreCase))
            {
                dest.Add(new SuggestionDto(
                    Title: "Set plea to Not Guilty",
                    Detail: "Not-guilty plea language detected.",
                    Body: "Record the plea as Not Guilty and confirm the next setting (pretrial / trial date) on the calendar.",
                    Confidence: 0.78));
            }
            else if (Regex.IsMatch(lower, @"\bno contest\b|\bnolo contendere\b", RegexOptions.IgnoreCase))
            {
                dest.Add(new SuggestionDto(
                    Title: "Set plea to No Contest",
                    Detail: "No-contest plea language detected.",
                    Body: "Record the plea as No Contest and confirm the agreed disposition.",
                    Confidence: 0.78));
            }

            if (Regex.IsMatch(lower, @"\b(dismiss(ed|al)|case dismissed|charge dismissed)\b", RegexOptions.IgnoreCase))
            {
                dest.Add(new SuggestionDto(
                    Title: "Update event status to Completed / Dismissed",
                    Detail: "Dismissal or completion language detected.",
                    Body: "Confirm which counts were dismissed and update case status, event status, and financials accordingly.",
                    Confidence: 0.82));
            }

            var fineMatch = Regex.Match(t, @"(?:fine|fined)[^\d]{0,40}(\d{1,5})", RegexOptions.IgnoreCase);
            var costMatch = Regex.Match(t, @"(?:costs?|court costs?)[^\d]{0,40}(\d{1,5})", RegexOptions.IgnoreCase);

            string? fineAmt = fineMatch.Success ? fineMatch.Groups[1].Value : null;
            string? costAmt = costMatch.Success ? costMatch.Groups[1].Value : null;

            if (fineAmt != null)
            {
                dest.Add(new SuggestionDto(
                    Title: $"Add ${fineAmt} fine",
                    Detail: "Fine amount detected in transcript.",
                    Body: $"Add a fine of ${fineAmt} to the event/case financials, matching the amount stated on the record.",
                    Confidence: 0.78));
            }

            if (costAmt != null)
            {
                dest.Add(new SuggestionDto(
                    Title: $"Add ${costAmt} court costs",
                    Detail: "Court-cost amount detected in transcript.",
                    Body: $"Add court costs of ${costAmt} to the event/case financials, matching the amount stated on the record.",
                    Confidence: 0.78));
            }

            // ✅ IMPORTANT: do NOT add generic financial suggestion unless payment/due language exists
            var hasPaymentLanguage = Regex.IsMatch(lower,
                @"payment plan|monthly payments|installments?|\bpay\b|\bpayment\b|\bdue\b|due in",
                RegexOptions.IgnoreCase);

            if (hasPaymentLanguage)
            {
                dest.Add(new SuggestionDto(
                    Title: "Review and update financial obligations",
                    Detail: "Payment/due language detected.",
                    Body: "Verify the total amount owed, the due date, and any payment-plan terms; update the financial module and event status as appropriate.",
                    Confidence: 0.70));
            }
        }
    }
}
