using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.RegularExpressions;

static class MigrationTool
{
    public sealed class MappingDto
    {
        public string Legacy { get; set; } = "";
        public string Pine { get; set; } = "";
        public string Confidence { get; set; } = "";
        public string Example { get; set; } = "";
    }

    public sealed class SynonymsRoot
    {
        public List<MappingDto> Mappings { get; set; } = new();
    }

    /// <summary>Load synonym mappings from output/synonyms.json (camelCase).</summary>
    public static List<MappingDto> LoadMappings(string synonymsPath)
    {
        string json = File.ReadAllText(synonymsPath);
        var options = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
        var root = JsonSerializer.Deserialize<SynonymsRoot>(json, options);
        return root?.Mappings ?? new List<MappingDto>();
    }

    /// <summary>Take first option when pine is "X or Y".</summary>
    static string FirstPineOption(string pine)
    {
        int or = pine.IndexOf(" or ");
        return or >= 0 ? pine.Substring(0, or).Trim() : pine.Trim();
    }

    /// <summary>Find closing paren for opening at openIndex; returns content between ( and ).</summary>
    static bool TryMatchParens(string s, int openIndex, out int contentStart, out int contentEnd)
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

    /// <summary>Legacy-like token (e.g. JW_*, KF_*, Cust_*, kf_* dotted ids) for REVIEW comment.</summary>
    static readonly Regex LegacyLikeToken = new Regex(@"\b(JW_[.\w]+|KF_[.\w]+|Cust_[.\w]+|kf_[.\w]+|Subpoena\.\w+)\b", RegexOptions.Compiled);

    /// <summary>Migrate one legacy fillpoint to pine. Adds // REVIEW: no mapping for [legacy] when something is left unmapped.</summary>
    public static string MigrateFillpoint(string legacyFillpoint, List<MappingDto> mappings)
    {
        if (string.IsNullOrWhiteSpace(legacyFillpoint))
            return legacyFillpoint + " // REVIEW: empty";

        if (!legacyFillpoint.TrimStart().StartsWith("%[") || !legacyFillpoint.TrimEnd().EndsWith("]"))
            return legacyFillpoint + " // REVIEW: invalid format";

        string raw = legacyFillpoint.Trim();
        int lastBracket = raw.LastIndexOf(']');
        string inner = raw.Substring(2, lastBracket - 2).Trim();
        bool needsReview = false;
        string? unmappedLegacy = null;

        // 0) FormatDate(CurrentDate(), ...) and CurrentDate() before variable replacement so "FormatDate" isn't replaced as a word
        inner = Regex.Replace(inner, @"FormatDate\s*\(\s*CurrentDate\s*\(\s*\)\s*,\s*[^)]+\)", "builtin.today.FormatDate(preset1)", RegexOptions.IgnoreCase);
        inner = Regex.Replace(inner, @"\bCurrentDate\s*\(\s*\)", "builtin.today", RegexOptions.IgnoreCase);

        // 0b) .IsEmpty -> .Any early so step 1b can replace legacy.Any with pine.Any
        inner = Regex.Replace(inner, @"\.IsEmpty\b", ".Any", RegexOptions.IgnoreCase);

        // 1) Variable/expression replacements (longest legacy first). Replace only whole-token so "Address" isn't replaced inside "RespondentAddress".
        var ordered = mappings
            .Where(m => !string.IsNullOrEmpty(m.Legacy) && !m.Legacy.Contains("…") && !m.Legacy.StartsWith("."))
            .OrderByDescending(m => m.Legacy.Length)
            .ToList();

        foreach (var m in ordered)
        {
            string legacy = m.Legacy.Trim();
            if (legacy.Contains("*") || legacy.Contains("(display)") || legacy == "If" || legacy == "ElseIf" || legacy == "Subdocument")
                continue; // skip wildcards and control-only
            string pine = FirstPineOption(m.Pine);
            if (string.IsNullOrEmpty(pine)) continue;
            // Whole-token match: not part of a longer identifier; case-insensitive so KF_/kf_ match
            // Don't replace "FormatDate" when it's the start of FormatDate(...)
            string pattern = legacy.Equals("FormatDate", StringComparison.OrdinalIgnoreCase)
                ? @"(?<![.\w])FormatDate(?![.\w(])"
                : @"(?<![.\w])" + Regex.Escape(legacy) + @"(?![.\w])";
            inner = Regex.Replace(inner, pattern, pine, RegexOptions.IgnoreCase);
        }

        // 1b) Replace legacy.Any with pine.Any (so .IsEmpty→.Any done later still gets variable replaced)
        foreach (var m in ordered)
        {
            string legacy = m.Legacy.Trim();
            if (legacy.Contains("*") || legacy.Contains("(display)")) continue;
            string pine = FirstPineOption(m.Pine);
            if (string.IsNullOrEmpty(pine)) continue;
            string pattern = @"(?<![.\w])" + Regex.Escape(legacy) + @"\.Any\b";
            inner = Regex.Replace(inner, pattern, pine + ".Any", RegexOptions.IgnoreCase);
        }

        // 2) Function wrappers: TitleCase( X ) -> X.SetCasing(Title), etc.
        var casingPatterns = new[] { ("TitleCase", "Title"), ("UpperCase", "Upper"), ("LowerCase", "Lower") };
        foreach (var (funcName, casing) in casingPatterns)
        {
            string pattern = funcName + "\\s*\\(";
            var match = Regex.Match(inner, pattern, RegexOptions.IgnoreCase);
            while (match.Success)
            {
                int openIdx = match.Index + match.Length - 1; // index of '('
                if (TryMatchParens(inner, openIdx, out int cs, out int ce))
                {
                    string arg = inner.Substring(cs, ce - cs).Trim();
                    string replacement = arg + ".SetCasing(" + casing + ")";
                    inner = inner.Substring(0, match.Index) + replacement + inner.Substring(ce + 1);
                    match = Regex.Match(inner, pattern, RegexOptions.IgnoreCase);
                }
                else
                {
                    needsReview = true;
                    break;
                }
            }
        }

        // 3) Initials( X , false ) -> entity.FormatName(FILI) when X is already a pine entity
        var initialsMatch = Regex.Match(inner, @"Initials\s*\(", RegexOptions.IgnoreCase);
        if (initialsMatch.Success)
        {
            int initialsIdx = initialsMatch.Index;
            int openParen = initialsIdx + initialsMatch.Length - 1; // index of '('
            if (TryMatchParens(inner, openParen, out int contentStart, out int contentEnd))
            {
                string content = inner.Substring(contentStart, contentEnd - contentStart);
                int commaFalse = content.IndexOf(", false", StringComparison.OrdinalIgnoreCase);
                if (commaFalse >= 0)
                {
                    string arg = content.Substring(0, commaFalse).Trim();
                    string fili;
                    if (arg.Contains(".first"))
                    {
                        int firstEnd = arg.IndexOf(".first") + 6;
                        fili = arg.Substring(0, firstEnd) + ".FormatName(FILI)";
                    }
                    else if (arg.Contains("cu."))
                        fili = arg + ".FormatName(FILI)";
                    else
                        fili = arg + " // REVIEW: Initials entity";
                    inner = inner.Substring(0, initialsIdx) + fili + inner.Substring(contentEnd + 1);
                    if (fili.Contains("REVIEW")) { needsReview = true; unmappedLegacy ??= arg; }
                }
            }
        }

        // 6) Any remaining .IsEmpty -> .Any (already done in 0b; this catches edge cases)
        inner = Regex.Replace(inner, @"\.IsEmpty\b", ".Any", RegexOptions.IgnoreCase);

        // Normalize spaces
        inner = Regex.Replace(inner, @"\s+", " ").Trim();

        // Detect remaining legacy-like tokens for REVIEW message
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

    /// <summary>Run migration: load synonyms, migrate 10–20 fillpoints from TAN Dismissal, Subpoena, Stipulation_to_Private, 348 Court Date Letter; print before/after; save to output/migrated_legacy.txt.</summary>
    public static void RunMigration(string outputDir)
    {
        string synonymsPath = Path.Combine(outputDir, "synonyms.json");
        if (!File.Exists(synonymsPath))
        {
            Console.WriteLine("synonyms.json not found at: " + synonymsPath);
            return;
        }

        var mappings = LoadMappings(synonymsPath);
        Console.WriteLine("Loaded " + mappings.Count + " synonym mappings from " + synonymsPath);

        var sources = new[]
        {
            Path.Combine(outputDir, "TAN_Dismissal_Letter", "legacy_cleaned.txt"),
            Path.Combine(outputDir, "Subpoena_Investigation", "legacy_cleaned.txt"),
            Path.Combine(outputDir, "Stipulation_to_Private", "legacy_cleaned.txt"),
            Path.Combine(outputDir, "348_-_Court_Date_Letter-Inquiry_Revised", "legacy_cleaned.txt"),
        };

        var toMigrate = new List<string>();
        foreach (string path in sources)
        {
            if (File.Exists(path))
                toMigrate.AddRange(File.ReadAllLines(path).Where(s => !string.IsNullOrWhiteSpace(s)).Take(6));
        }
        toMigrate = toMigrate.Distinct().Take(20).ToList();

        if (toMigrate.Count == 0)
        {
            Console.WriteLine("No legacy_cleaned.txt found under " + outputDir + ". Run extractor first.");
            return;
        }

        Console.WriteLine("\n--- Before / After (top " + toMigrate.Count + " legacy fillpoints) ---\n");
        var migrated = new List<string>();

        foreach (string legacy in toMigrate)
        {
            string pine = MigrateFillpoint(legacy, mappings);
            Console.WriteLine("BEFORE: " + legacy);
            Console.WriteLine("AFTER:  " + pine);
            Console.WriteLine();
            migrated.Add(pine);
        }

        string outPath = Path.Combine(outputDir, "migrated_legacy.txt");
        File.WriteAllLines(outPath, migrated);
        Console.WriteLine("Saved " + migrated.Count + " migrated fillpoints to " + outPath);
    }
}
