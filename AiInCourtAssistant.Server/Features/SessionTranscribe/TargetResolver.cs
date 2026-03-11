// Server/Features/SessionTranscribe/TargetResolver.cs
using System.Text.RegularExpressions;

namespace AiInCourtAssistant.Server.Features.SessionTranscribe;

internal sealed class TargetResolver
{
    private readonly Dictionary<string, int> _caseFull = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, int> _typeSeq = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, int> _uniqueSeq = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, int> _fullName = new(StringComparer.OrdinalIgnoreCase);

    private TargetResolver() { }

    public static TargetResolver From(IEnumerable<(int EventId, string? CaseNo, string? Label)> hints)
    {
        var r = new TargetResolver();
        var seqCounts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);

        foreach (var h in hints)
        {
            // case number keys
            var caseNo = NormalizeCaseNo(h.CaseNo ?? ExtractCaseNoFromLabel(h.Label));
            if (!string.IsNullOrEmpty(caseNo))
            {
                // 24CR00123 → pieces
                var m = CaseRx.Match(caseNo);
                if (m.Success)
                {
                    var yy = m.Groups["yy"].Value;
                    var typ = m.Groups["typ"].Value;
                    var seq = m.Groups["seq"].Value;

                    var full = NormalizeCaseNo($"{yy}-{typ}-{seq}");
                    var typeSq = NormalizeCaseNo($"{typ}-{seq}");
                    var onlySq = NormalizeCaseNo(seq);

                    r._caseFull[full] = h.EventId;
                    r._typeSeq[typeSq] = h.EventId;
                    seqCounts[onlySq] = seqCounts.TryGetValue(onlySq, out var c) ? c + 1 : 1;
                }
            }

            // name key (from "18-CR-… ~ Last, First …")
            var name = LastCommaFirstToFull(h.Label);
            var fullNorm = NormalizeName(name);
            if (!string.IsNullOrEmpty(fullNorm))
                r._fullName[fullNorm] = h.EventId;
        }

        // unique sequences only
        foreach (var kv in seqCounts.Where(kv => kv.Value == 1 && kv.Key.Length >= 5))
            r._uniqueSeq[kv.Key] = 0; // event will be resolved below

        // Fill event for unique seq by reverse look-up in _typeSeq/_caseFull
        foreach (var key in r._uniqueSeq.Keys.ToList())
        {
            var hit = r._typeSeq.FirstOrDefault(kv => kv.Key.EndsWith(key, StringComparison.OrdinalIgnoreCase));
            if (!hit.Equals(default(KeyValuePair<string, int>))) r._uniqueSeq[key] = hit.Value;
            else
            {
                var hit2 = r._caseFull.FirstOrDefault(kv => kv.Key.EndsWith(key, StringComparison.OrdinalIgnoreCase));
                if (!hit2.Equals(default(KeyValuePair<string, int>))) r._uniqueSeq[key] = hit2.Value;
            }
        }

        return r;
    }

    public bool TryResolve(string text, out int eventId, out string why)
    {
        eventId = 0; why = "";
        if (string.IsNullOrWhiteSpace(text)) return false;

        var collapsed = NormalizeCaseNo(text);
        var nameText = NormalizeName(text);

        int bestId = 0, bestScore = -1; string reason = "";

        void Consider(string haystack, string key, int id, string r, int weight)
        {
            if (string.IsNullOrEmpty(key) || id <= 0) return;
            int pos = haystack.LastIndexOf(key, StringComparison.Ordinal);
            if (pos < 0) return;
            int score = weight * 1000 + pos;
            if (score > bestScore) { bestScore = score; bestId = id; reason = r; }
        }

        foreach (var kv in _caseFull) Consider(collapsed, kv.Key, kv.Value, "case#", 5);
        foreach (var kv in _typeSeq) Consider(collapsed, kv.Key, kv.Value, "type-seq", 4);
        foreach (var kv in _uniqueSeq) Consider(collapsed, kv.Key, kv.Value, "seq-unique", 3);
        foreach (var kv in _fullName) Consider(nameText, kv.Key, kv.Value, "full-name", 2);

        if (bestId > 0) { eventId = bestId; why = reason; return true; }
        return false;
    }

    // ---------- helpers ----------
    private static readonly Regex CaseRx = new(
        @"(?<!\d)(?<yy>\d{2})\s*[-\s]?(?<typ>[A-Z]{1,3})\s*[-\s]?(?<seq>\d{3,7})(?!\d)",
        RegexOptions.Compiled | RegexOptions.IgnoreCase);

    private static string NormalizeCaseNo(string? s)
        => string.IsNullOrWhiteSpace(s) ? "" : Regex.Replace(s.ToUpperInvariant(), "[^A-Z0-9]", "");

    private static string NormalizeName(string? s)
    {
        if (string.IsNullOrWhiteSpace(s)) return "";
        var lettersSpaces = Regex.Replace(s.ToUpperInvariant(), "[^A-Z ]", " ");
        return Regex.Replace(lettersSpaces, "\\s+", " ").Trim();
    }

    private static string LastCommaFirstToFull(string? label)
    {
        if (string.IsNullOrWhiteSpace(label)) return "";
        // "… ~ LAST, First Middle"
        var parts = label.Split('~');
        var right = parts.Length > 1 ? parts[1].Trim() : "";
        var segs = right.Split(',', 2, StringSplitOptions.TrimEntries);
        if (segs.Length < 2) return "";
        var last = segs[0]; var first = segs[1].Split(' ', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? "";
        return $"{first} {last}";
    }

    private static string? ExtractCaseNoFromLabel(string? label)
    {
        if (string.IsNullOrWhiteSpace(label)) return null;
        var m = Regex.Match(label, @"\b\d{2}[-\s]?[A-Z]{1,3}[-\s]?\d{3,6}\b", RegexOptions.IgnoreCase);
        return m.Success ? m.Value : null;
    }
}
