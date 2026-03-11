using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using AiInCourtAssistant.Server.Features.SessionTranscribe.Models; // TranscriptSegment
using SharedSuggestionDto = AiInCourtAssistant.Shared.Models.SuggestionDto;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe
{
    public sealed class RuleSuggestionEngine : ISuggestionEngine
    {
        public Task<IReadOnlyList<SharedSuggestionDto>> GenerateAsync(
            Guid tenantId,
            int eventId,
            IReadOnlyList<TranscriptSegment> segments,
            CancellationToken ct)
        {
            var list = new List<SharedSuggestionDto>();
            var text = string.Join(" ", segments?.Select(s => s.Text) ?? Enumerable.Empty<string>());

            if (string.IsNullOrWhiteSpace(text))
                return Task.FromResult<IReadOnlyList<SharedSuggestionDto>>(list);

            var t = text;
            var lower = t.ToLowerInvariant();

            // --- FTA / nonappearance ---
            var isFta =
                Regex.IsMatch(lower, @"\b(fta|failure to appear|failed to appear|non[- ]appearance)\b", RegexOptions.IgnoreCase) ||
                Regex.IsMatch(lower, @"\b(defendant|she|he)\s+(was|is|remains)\s+(not present|absent)\b", RegexOptions.IgnoreCase);

            if (isFta)
            {
                list.Add(new SharedSuggestionDto(
                    Title: "Update event status to FTA",
                    Detail: "FTA / nonappearance language detected.",
                    Body: "Verify attendance and prior notice. If FTA is confirmed, update the event status to FTA and ensure the docket reflects the nonappearance.",
                    Confidence: 0.85));
            }

            // --- Bench warrant ---
            if (Regex.IsMatch(lower, @"bench warrant|warrant for (her|his) arrest|issue a bench warrant|bench warrant will issue", RegexOptions.IgnoreCase))
            {
                list.Add(new SharedSuggestionDto(
                    Title: "Update case status to Bench Warrant",
                    Detail: "Bench-warrant language detected.",
                    Body: "Confirm the warrant is entered, transmitted as required, and that the case/event status reflects the bench warrant.",
                    Confidence: 0.80));
            }

            // --- Bond / bail amount (supports: "bond $500" AND "$500 bond") ---
            var bondMatch1 = Regex.Match(t, @"\b(?:bond|bail)\b[^\d]{0,40}(\d{1,6})", RegexOptions.IgnoreCase);
            var bondMatch2 = Regex.Match(t,
                @"\b\$(\d{1,6})\b[^\w]{0,10}\b(?:bond|bail)\b|\b(\d{1,6})\b[^\w]{0,10}\b(?:bond|bail)\b",
                RegexOptions.IgnoreCase);

            string? bondAmt = null;
            if (bondMatch1.Success) bondAmt = bondMatch1.Groups[1].Value;
            else if (bondMatch2.Success) bondAmt = bondMatch2.Groups[1].Success ? bondMatch2.Groups[1].Value : bondMatch2.Groups[2].Value;

            if (!string.IsNullOrWhiteSpace(bondAmt))
            {
                list.Add(new SharedSuggestionDto(
                    Title: $"Set bond amount to ${bondAmt}",
                    Detail: "Bond / bail amount detected in transcript.",
                    Body: $"Set the bond amount to ${bondAmt} on the case/event, consistent with the order stated on the record.",
                    Confidence: 0.78));
            }

            // --- Proof of insurance / compliance ---
            if (Regex.IsMatch(lower, @"proof of insurance|proof of correction|proof of renewal|bring proof of", RegexOptions.IgnoreCase))
            {
                list.Add(new SharedSuggestionDto(
                    Title: "Set a condition to bring proof of insurance",
                    Detail: "Transcript references a proof or compliance requirement.",
                    Body: "Add a condition requiring the defendant to bring the specified proof (insurance, correction, renewal, etc.) by the review date, and track that requirement on the calendar and in case notes.",
                    Confidence: 0.80));
            }

            // --- Community service ---
            var csMatch = Regex.Match(t, @"(\d{1,3})\s+hours?\s+of\s+community\s+service", RegexOptions.IgnoreCase);
            if (csMatch.Success)
            {
                var hours = csMatch.Groups[1].Value;
                list.Add(new SharedSuggestionDto(
                    Title: $"Add a condition for {hours} hours of community service",
                    Detail: "Community-service requirement detected.",
                    Body: $"Add a condition requiring the defendant to complete {hours} hours of community service, linked to this event/case.",
                    Confidence: 0.78));
            }

            // --- Traffic school ---
            if (Regex.IsMatch(lower, @"traffic school|driving school|defensive driving school", RegexOptions.IgnoreCase))
            {
                list.Add(new SharedSuggestionDto(
                    Title: "Add a condition for traffic school",
                    Detail: "Traffic-school option or requirement detected.",
                    Body: "Add a condition requiring the defendant to complete the specified traffic-school program and link completion to the case outcome.",
                    Confidence: 0.78));
            }

            // --- Continuance / next date ---
            var hasContinueLanguage = Regex.IsMatch(lower,
                @"\bcontinuance\b|\bcontinued\b|\bcontinued for\b|\bcontinued to\b|\bnext court date\b|\bnext hearing\b|\bpretrial\b|\bstatus review\b|\breview hearing\b|\bset for\b|\bscheduled for\b",
                RegexOptions.IgnoreCase);

            if (hasContinueLanguage)
            {
                list.Add(new SharedSuggestionDto(
                    Title: "Update event status to Continued",
                    Detail: "Continuance or review language detected.",
                    Body: "Set the event status to Continued and make sure the calendar reflects any new review or status dates.",
                    Confidence: 0.75));

                // numeric date like 01/10/2026 at 9:00 AM
                var numeric = Regex.Match(t,
                    @"\b(\d{1,2}/\d{1,2}/\d{4})(?:\s+(?:at)?\s*([0-9: ]+(?:AM|PM)))?",
                    RegexOptions.IgnoreCase);

                // long date like February 8, 2026 at 10:30 AM
                var longDate = Regex.Match(t,
                    @"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}(?:[^0-9APap]{0,20}([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM)|[0-9]{1,2}\s*(?:AM|PM)))?",
                    RegexOptions.IgnoreCase);

                string? dateTimeText = null;

                if (numeric.Success)
                {
                    var date = numeric.Groups[1].Value;
                    var time = numeric.Groups[2].Success ? numeric.Groups[2].Value.Trim() : "";
                    dateTimeText = string.IsNullOrWhiteSpace(time) ? date : $"{date} {time}";
                }
                else if (longDate.Success)
                {
                    dateTimeText = longDate.Value.Trim();
                }

                if (!string.IsNullOrWhiteSpace(dateTimeText))
                {
                    var label =
                        Regex.IsMatch(lower, @"status review", RegexOptions.IgnoreCase) ? "status-review event" :
                        Regex.IsMatch(lower, @"pretrial", RegexOptions.IgnoreCase) ? "pretrial conference" :
                        "event";

                    list.Add(new SharedSuggestionDto(
                        Title: $"Add {label} for {dateTimeText}",
                        Detail: "Future court date/time detected.",
                        Body: $"Create a {label} on the calendar for {dateTimeText}, matching the date and time stated on the record.",
                        Confidence: 0.72));
                }
            }

            // --- Pleas (including NOT GUILTY) ---
            if (Regex.IsMatch(lower, @"\bplead(?:s|ed|ing)?\s+guilty\b|\bplea[, ]+guilty\b", RegexOptions.IgnoreCase))
            {
                list.Add(new SharedSuggestionDto(
                    Title: "Set plea to Guilty",
                    Detail: "Guilty-plea language detected.",
                    Body: "Record the plea as Guilty for the relevant counts and link it to this event.",
                    Confidence: 0.78));
            }
            else if (Regex.IsMatch(lower, @"\bplea of not guilty\b|\bplead(?:s|ed|ing)?\s+not guilty\b|\benters a plea of not guilty\b", RegexOptions.IgnoreCase))
            {
                list.Add(new SharedSuggestionDto(
                    Title: "Set plea to Not Guilty",
                    Detail: "Not-guilty plea language detected.",
                    Body: "Record the plea as Not Guilty and confirm the next setting (pretrial/status check) is calendared.",
                    Confidence: 0.78));
            }
            else if (Regex.IsMatch(lower, @"\bno contest\b|\bnolo contendere\b", RegexOptions.IgnoreCase))
            {
                list.Add(new SharedSuggestionDto(
                    Title: "Set plea to No Contest",
                    Detail: "No-contest plea language detected.",
                    Body: "Record the plea as No Contest and confirm the agreed disposition.",
                    Confidence: 0.78));
            }

            // --- Dismissals ---
            if (Regex.IsMatch(lower, @"\b(dismiss(ed|al)|case dismissed|charge dismissed)\b", RegexOptions.IgnoreCase))
            {
                list.Add(new SharedSuggestionDto(
                    Title: "Update event status to Completed / Dismissed",
                    Detail: "Dismissal or completion language detected.",
                    Body: "Confirm which counts were dismissed and update case status, event status, and financials accordingly.",
                    Confidence: 0.82));
            }

            // --- Fines / costs ---
            var fineMatch = Regex.Match(t, @"(?:fine|fined)[^\d]{0,40}(\d{1,5})", RegexOptions.IgnoreCase);
            var costMatch = Regex.Match(t, @"(?:costs?|court costs?)[^\d]{0,40}(\d{1,5})", RegexOptions.IgnoreCase);

            string? fineAmt = fineMatch.Success ? fineMatch.Groups[1].Value : null;
            string? costAmt = costMatch.Success ? costMatch.Groups[1].Value : null;

            if (fineAmt != null)
            {
                list.Add(new SharedSuggestionDto(
                    Title: $"Add ${fineAmt} fine",
                    Detail: "Fine amount detected in transcript.",
                    Body: $"Add a fine of ${fineAmt} to the event/case financials, matching the amount stated on the record.",
                    Confidence: 0.78));
            }

            if (costAmt != null)
            {
                list.Add(new SharedSuggestionDto(
                    Title: $"Add ${costAmt} court costs",
                    Detail: "Court-cost amount detected in transcript.",
                    Body: $"Add court costs of ${costAmt} to the event/case financials, matching the amount stated on the record.",
                    Confidence: 0.78));
            }

            // Only if payment/due language exists
            var hasPaymentLanguage = Regex.IsMatch(lower,
                @"payment plan|monthly payments|installments?|\bpay\b|\bpayment\b|\bdue\b|due in",
                RegexOptions.IgnoreCase);

            if (hasPaymentLanguage)
            {
                list.Add(new SharedSuggestionDto(
                    Title: "Review and update financial obligations",
                    Detail: "Payment/due language detected.",
                    Body: "Verify the total amount owed, the due date, and any payment-plan terms; update the financial module and event status as appropriate.",
                    Confidence: 0.70));
            }

            return Task.FromResult<IReadOnlyList<SharedSuggestionDto>>(list);
        }
    }
}
