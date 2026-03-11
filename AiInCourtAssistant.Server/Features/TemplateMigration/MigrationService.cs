using System.Text.Json;
using System.Text.RegularExpressions;

namespace AiInCourtAssistant.Server.Features.TemplateMigration;

public sealed class MigrationMappingDto
{
    public string Legacy { get; set; } = "";
    public string Pine { get; set; } = "";
    public string? Confidence { get; set; }
    public string? Example { get; set; }
}

public sealed class SynonymsRootDto
{
    public List<MigrationMappingDto> Mappings { get; set; } = new();
}

/// <summary>Port of phase1 MigrationTool: load synonyms, migrate legacy fillpoints, extract fillpoints from RTF.</summary>
public sealed class MigrationService
{
    private readonly IWebHostEnvironment _env;
    private readonly ILogger<MigrationService> _log;
    private readonly RtfToHtmlService _rtfToHtml;
    private List<MigrationMappingDto>? _mappings;
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase
    };

    private static readonly Regex LegacyLikeToken = new(
        @"\b(JW_[.\w]+|KF_[.\w]+|Cust_[.\w]+|kf_[.\w]+|Subpoena\.\w+)\b",
        RegexOptions.Compiled);

    public MigrationService(IWebHostEnvironment env, ILogger<MigrationService> log, RtfToHtmlService rtfToHtml)
    {
        _env = env;
        _log = log;
        _rtfToHtml = rtfToHtml;
    }

    /// <summary>Convert RTF to HTML (RtfPipe + CodePages). Returns HTML string or null on failure.</summary>
    public string? RtfToHtml(string? rtfContent)
    {
        if (string.IsNullOrEmpty(rtfContent)) return null;
        var (html, _) = _rtfToHtml.RtfToHtmlWithHighlight(rtfContent.TrimStart('\uFEFF'));
        return html;
    }

    /// <summary>Load mappings from App_Data/synonyms.json (copy from phase1-jda-to-pine-extractor/output/synonyms.json).</summary>
    public List<MigrationMappingDto> GetMappings()
    {
        if (_mappings != null) return _mappings;

        var path = Path.Combine(_env.ContentRootPath, "App_Data", "synonyms.json");
        if (!File.Exists(path))
        {
            _log.LogWarning("synonyms.json not found at {Path}. Copy from phase1-jda-to-pine-extractor/output/synonyms.json", path);
            _mappings = new List<MigrationMappingDto>();
            return _mappings;
        }

        var json = File.ReadAllText(path);
        var root = JsonSerializer.Deserialize<SynonymsRootDto>(json, JsonOptions);
        _mappings = root?.Mappings ?? new List<MigrationMappingDto>();
        _log.LogInformation("Loaded {Count} synonym mappings from synonyms.json", _mappings.Count);
        return _mappings;
    }

    /// <summary>Extract legacy fillpoints %[ ... ] from RTF content (bracket-matching, skip RTF controls).</summary>
    public static List<string> ExtractFillpointsFromRtf(string rtfContent)
    {
        var fillpoints = new List<string>();
        if (string.IsNullOrEmpty(rtfContent)) return fillpoints;

        int i = 0;
        while (i < rtfContent.Length)
        {
            int start = rtfContent.IndexOf("%[", i, StringComparison.Ordinal);
            if (start < 0) break;

            int depth = 1;
            int pos = start + 2;

            while (pos < rtfContent.Length && depth > 0)
            {
                char c = rtfContent[pos];

                if (c == '\\')
                {
                    pos++;
                    if (pos >= rtfContent.Length) break;
                    if (rtfContent[pos] == '\'' && pos + 2 < rtfContent.Length &&
                        IsHex(rtfContent[pos + 1]) && IsHex(rtfContent[pos + 2]))
                    {
                        pos += 3;
                        continue;
                    }
                    if (char.IsLetter(rtfContent[pos]))
                    {
                        while (pos < rtfContent.Length && char.IsLetter(rtfContent[pos])) pos++;
                        if (pos < rtfContent.Length && rtfContent[pos] == '-') pos++;
                        while (pos < rtfContent.Length && char.IsDigit(rtfContent[pos])) pos++;
                    }
                    else
                        pos++;
                    continue;
                }

                if (c == '[') depth++;
                else if (c == ']') depth--;
                pos++;
            }

            if (depth != 0) { i = start + 1; continue; }

            int endInclusive = pos - 1;
            string raw = rtfContent.Substring(start, endInclusive - start + 1);
            raw = Regex.Replace(raw, @"\\[^ ]+", " ");
            raw = Regex.Replace(raw, @"\{|\}", " ");
            raw = Regex.Replace(raw, @"\s+", " ").Trim();

            if (raw.StartsWith("%[") && raw.EndsWith("]") && raw.Length > 8 && !raw.StartsWith("%[}{"))
                fillpoints.Add(raw);

            i = endInclusive + 1;
        }

        return fillpoints.Distinct().ToList();
    }

    private static bool IsHex(char c) =>
        (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');

    private static bool LooksLikeRtf(string? s)
    {
        if (string.IsNullOrEmpty(s)) return false;
        s = s.TrimStart('\uFEFF').TrimStart();
        return s.StartsWith("{\\rtf", StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>Migrate: extract fillpoints, migrate each. When fullRtf=true, reassemble original RTF (preserve headers/footers). When false, output only the migrated fillpoints list.</summary>
    public MigrateResult MigrateText(string? legacyText, string? rtfContent, bool fullRtf = true)
    {
        var mappings = GetMappings();
        string source = !string.IsNullOrWhiteSpace(rtfContent) ? rtfContent : (legacyText ?? "");
        if (string.IsNullOrWhiteSpace(source))
            return new MigrateResult { LegacyText = "", MigratedText = "", Fillpoints = new List<FillpointPair>(), ReviewTokens = new List<string>() };

        var fillpoints = ExtractFillpointsFromRtf(source);
        var reviewTokens = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        if (fillpoints.Count == 0)
        {
            // No %[ found: treat whole as one fillpoint if it looks like legacy, else pass through
            string trimmed = (legacyText ?? source).Trim();
            if (trimmed.StartsWith("%[") && trimmed.EndsWith("]"))
            {
                var migrated = MigrateFillpoint(trimmed, mappings);
                ExtractReviewToken(migrated, reviewTokens);
                var single = new MigrateResult
                {
                    LegacyText = trimmed,
                    MigratedText = migrated,
                    Fillpoints = new List<FillpointPair> { new(trimmed, migrated) },
                    ReviewTokens = reviewTokens.ToList()
                };
                try
                {
                    var (lh, _) = _rtfToHtml.RtfToHtmlWithHighlight(trimmed);
                    single.LegacyHtml = !string.IsNullOrEmpty(lh) ? lh : RtfToHtmlService.PlainToHtmlWithHighlight(trimmed);
                    var (mh, _) = _rtfToHtml.RtfToHtmlWithHighlight(migrated);
                    single.MigratedHtml = !string.IsNullOrEmpty(mh) ? mh : RtfToHtmlService.PlainToHtmlWithHighlight(migrated);
                }
                catch (Exception ex)
                {
                    _log.LogWarning(ex, "Single fillpoint RTF to HTML failed.");
                    single.LegacyHtml = RtfToHtmlService.PlainToHtmlWithHighlight(trimmed);
                    single.MigratedHtml = RtfToHtmlService.PlainToHtmlWithHighlight(migrated);
                }
                return single;
            }
            var passThrough = new MigrateResult { LegacyText = source, MigratedText = source, Fillpoints = new List<FillpointPair>(), ReviewTokens = new List<string>() };
            if (!string.IsNullOrEmpty(source))
            {
                try
                {
                    var (html, _) = _rtfToHtml.RtfToHtmlWithHighlight(source);
                    var fallback = RtfToHtmlService.PlainToHtmlWithHighlight(source);
                    passThrough.LegacyHtml = !string.IsNullOrEmpty(html) ? html : fallback;
                    passThrough.MigratedHtml = passThrough.LegacyHtml;
                }
                catch (Exception ex)
                {
                    _log.LogWarning(ex, "Pass-through RTF to HTML failed.");
                    passThrough.LegacyHtml = RtfToHtmlService.PlainToHtmlWithHighlight(source);
                    passThrough.MigratedHtml = passThrough.LegacyHtml;
                }
            }
            return passThrough;
        }

        var pairs = new List<FillpointPair>();
        foreach (string fp in fillpoints)
        {
            string migrated = MigrateFillpoint(fp, mappings);
            ExtractReviewToken(migrated, reviewTokens);
            pairs.Add(new FillpointPair(fp, migrated));
        }

        string legacyOut;
        string migratedText;

        if (fullRtf)
        {
            // Reassemble full RTF: replace each fillpoint in place, preserve all non-fillpoint content (headers, footers, formatting)
            var replacements = new List<(int start, int length, string replacement)>();
            foreach (string fp in fillpoints)
            {
                var pair = pairs.First(p => p.Legacy == fp);
                int idx = 0;
                while ((idx = source.IndexOf(fp, idx, StringComparison.Ordinal)) >= 0)
                {
                    replacements.Add((idx, fp.Length, pair.Migrated));
                    idx += fp.Length;
                }
            }
            replacements.Sort((a, b) => b.start.CompareTo(a.start));
            var sb = new System.Text.StringBuilder(source);
            foreach (var (start, length, replacement) in replacements)
                sb.Remove(start, length).Insert(start, replacement);
            migratedText = sb.ToString();
            legacyOut = source;
        }
        else
        {
            // Fillpoints only: output just the legacy and migrated fillpoint lists (one per line)
            legacyOut = string.Join(Environment.NewLine, fillpoints);
            migratedText = string.Join(Environment.NewLine, pairs.Select(p => p.Migrated));
        }

        var result = new MigrateResult
        {
            LegacyText = legacyOut,
            MigratedText = migratedText,
            Fillpoints = pairs,
            ReviewTokens = reviewTokens.ToList()
        };

        // Always generate HTML for display so panes show formatted text (not raw RTF). Ensures LegacyHtml and MigratedHtml are always set for RTF-like content.
        if (!string.IsNullOrEmpty(legacyOut))
        {
            try
            {
                var (legacyHtml, legErr) = _rtfToHtml.RtfToHtmlWithHighlight(legacyOut);
                if (!string.IsNullOrEmpty(legErr)) _log.LogWarning("Legacy RTF to HTML: {Err}", legErr);
                result.LegacyHtml = !string.IsNullOrEmpty(legacyHtml) ? legacyHtml : RtfToHtmlService.PlainToHtmlWithHighlight(legacyOut);
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "Legacy RTF to HTML failed; using plain fallback.");
                result.LegacyHtml = RtfToHtmlService.PlainToHtmlWithHighlight(legacyOut);
            }
        }

        if (!string.IsNullOrEmpty(migratedText))
        {
            try
            {
                var (migHtml, migErr) = _rtfToHtml.RtfToHtmlWithHighlight(migratedText);
                if (!string.IsNullOrEmpty(migErr)) _log.LogWarning("Migrated RTF to HTML: {Err}", migErr);
                result.MigratedHtml = !string.IsNullOrEmpty(migHtml) ? migHtml : RtfToHtmlService.PlainToHtmlWithHighlight(migratedText);
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "Migrated RTF to HTML failed; using plain fallback.");
                result.MigratedHtml = RtfToHtmlService.PlainToHtmlWithHighlight(migratedText);
            }
        }

        return result;
    }

    private static void ExtractReviewToken(string migrated, HashSet<string> reviewTokens)
    {
        var m = Regex.Match(migrated, @"//\s*REVIEW:\s*no mapping for\s+(.+)");
        if (m.Success) reviewTokens.Add(m.Groups[1].Value.Trim());
    }

    public string MigrateFillpoint(string legacyFillpoint, List<MigrationMappingDto>? mappings = null)
    {
        var list = mappings ?? GetMappings();
        return MigrateFillpointCore(legacyFillpoint, list);
    }

    private static string FirstPineOption(string pine)
    {
        int or = pine.IndexOf(" or ", StringComparison.Ordinal);
        return or >= 0 ? pine.Substring(0, or).Trim() : pine.Trim();
    }

    private static bool TryMatchParens(string s, int openIndex, out int contentStart, out int contentEnd)
    {
        contentStart = openIndex + 1;
        contentEnd = -1;
        int depth = 1;
        for (int i = openIndex + 1; i < s.Length; i++)
        {
            if (s[i] == '(') depth++;
            else if (s[i] == ')')
            {
                depth--;
                if (depth == 0) { contentEnd = i; return true; }
            }
        }
        return false;
    }

    private static string MigrateFillpointCore(string legacyFillpoint, List<MigrationMappingDto> mappings)
    {
        if (string.IsNullOrWhiteSpace(legacyFillpoint))
            return legacyFillpoint + " // REVIEW: empty";

        if (!legacyFillpoint.TrimStart().StartsWith("%[", StringComparison.Ordinal) ||
            !legacyFillpoint.TrimEnd().EndsWith("]", StringComparison.Ordinal))
            return legacyFillpoint + " // REVIEW: invalid format";

        string raw = legacyFillpoint.Trim();
        int lastBracket = raw.LastIndexOf(']');
        string inner = raw.Substring(2, lastBracket - 2).Trim();
        bool needsReview = false;
        string? unmappedLegacy = null;

        inner = Regex.Replace(inner, @"FormatDate\s*\(\s*CurrentDate\s*\(\s*\)\s*,\s*[^)]+\)", "builtin.today.FormatDate(preset1)", RegexOptions.IgnoreCase);
        inner = Regex.Replace(inner, @"\bCurrentDate\s*\(\s*\)", "builtin.today", RegexOptions.IgnoreCase);
        inner = Regex.Replace(inner, @"\.IsEmpty\b", ".Any", RegexOptions.IgnoreCase);

        var ordered = mappings
            .Where(m => !string.IsNullOrEmpty(m.Legacy) && !m.Legacy.Contains("…") && !m.Legacy.StartsWith("."))
            .OrderByDescending(m => m.Legacy.Length)
            .ToList();

        foreach (var m in ordered)
        {
            string legacy = m.Legacy.Trim();
            if (legacy.Contains('*') || legacy.Contains("(display)") || legacy == "If" || legacy == "ElseIf" || legacy == "Subdocument")
                continue;
            string pine = FirstPineOption(m.Pine);
            if (string.IsNullOrEmpty(pine)) continue;
            string pattern = string.Equals(legacy, "FormatDate", StringComparison.OrdinalIgnoreCase)
                ? @"(?<![.\w])FormatDate(?![.\w(])"
                : @"(?<![.\w])" + Regex.Escape(legacy) + @"(?![.\w])";
            inner = Regex.Replace(inner, pattern, pine, RegexOptions.IgnoreCase);
        }

        foreach (var m in ordered)
        {
            string legacy = m.Legacy.Trim();
            if (legacy.Contains('*') || legacy.Contains("(display)")) continue;
            string pine = FirstPineOption(m.Pine);
            if (string.IsNullOrEmpty(pine)) continue;
            inner = Regex.Replace(inner, @"(?<![.\w])" + Regex.Escape(legacy) + @"\.Any\b", pine + ".Any", RegexOptions.IgnoreCase);
        }

        foreach (var (funcName, casing) in new[] { ("TitleCase", "Title"), ("UpperCase", "Upper"), ("LowerCase", "Lower") })
        {
            string pattern = funcName + @"\s*\(";
            var match = Regex.Match(inner, pattern, RegexOptions.IgnoreCase);
            while (match.Success)
            {
                int openIdx = match.Index + match.Length - 1;
                if (TryMatchParens(inner, openIdx, out int cs, out int ce))
                {
                    string arg = inner.Substring(cs, ce - cs).Trim();
                    inner = inner.Substring(0, match.Index) + arg + ".SetCasing(" + casing + ")" + inner.Substring(ce + 1);
                    match = Regex.Match(inner, pattern, RegexOptions.IgnoreCase);
                }
                else { needsReview = true; break; }
            }
        }

        var initialsMatch = Regex.Match(inner, @"Initials\s*\(", RegexOptions.IgnoreCase);
        if (initialsMatch.Success)
        {
            int openParen = initialsMatch.Index + initialsMatch.Length - 1;
            if (TryMatchParens(inner, openParen, out int contentStart, out int contentEnd))
            {
                string content = inner.Substring(contentStart, contentEnd - contentStart);
                int commaFalse = content.IndexOf(", false", StringComparison.OrdinalIgnoreCase);
                if (commaFalse >= 0)
                {
                    string arg = content.Substring(0, commaFalse).Trim();
                    string fili = arg.Contains(".first")
                        ? arg.Substring(0, arg.IndexOf(".first") + 6) + ".FormatName(FILI)"
                        : arg.Contains("cu.") ? arg + ".FormatName(FILI)" : arg + " // REVIEW: Initials entity";
                    inner = inner.Substring(0, initialsMatch.Index) + fili + inner.Substring(contentEnd + 1);
                    if (fili.Contains("REVIEW")) { needsReview = true; unmappedLegacy ??= arg; }
                }
            }
        }

        inner = Regex.Replace(inner, @"\.IsEmpty\b", ".Any", RegexOptions.IgnoreCase);
        inner = Regex.Replace(inner, @"\s+", " ").Trim();

        if (unmappedLegacy == null)
        {
            var legacyMatch = LegacyLikeToken.Match(inner);
            if (legacyMatch.Success) unmappedLegacy = legacyMatch.Groups[1].Value;
        }

        string result = "@[" + inner + "]";
        if (needsReview)
            result += unmappedLegacy != null ? " // REVIEW: no mapping for " + unmappedLegacy : " // REVIEW: no mapping or semantics";
        return result;
    }
}

public sealed record FillpointPair(string Legacy, string Migrated);

public sealed class MigrateResult
{
    public string LegacyText { get; set; } = "";
    public string MigratedText { get; set; } = "";
    public List<FillpointPair> Fillpoints { get; set; } = new();
    public List<string> ReviewTokens { get; set; } = new();
    /// <summary>Rendered HTML for legacy (with fillpoint highlights); null if conversion failed or not RTF.</summary>
    public string? LegacyHtml { get; set; }
    /// <summary>Rendered HTML for migrated (with fillpoint highlights); null if conversion failed or not RTF.</summary>
    public string? MigratedHtml { get; set; }
}
