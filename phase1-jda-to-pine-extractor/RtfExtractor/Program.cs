using System;
using System.IO;
using System.Text.RegularExpressions;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;

class Program
{
    static void Main(string[] args)
    {
        if (args.Length == 0)
        {
            Console.WriteLine("Usage: dotnet run <path-to-input-folder>");
            Console.WriteLine("   or:  dotnet run migrate [output-folder]");
            return;
        }

        if (args[0].Equals("migrate", StringComparison.OrdinalIgnoreCase))
        {
            string outputDir = args.Length > 1 ? args[1] : Path.Combine("..", "output");
            MigrationTool.RunMigration(outputDir);
            return;
        }

        string inputPath = args[0];

        if (!Directory.Exists(inputPath))
        {
            Console.WriteLine("Input folder not found: " + inputPath);
            return;
        }

        ProcessInputFolder(inputPath);
    }

    static void ProcessInputFolder(string baseInputPath)
    {
        string legacyPath = Path.Combine(baseInputPath, "legacy");
        string pinePath   = Path.Combine(baseInputPath, "pine");

        if (!Directory.Exists(legacyPath) || !Directory.Exists(pinePath))
        {
            Console.WriteLine("Expected subfolders 'legacy' and 'pine' inside input folder.");
            return;
        }

        var legacyFiles = Directory.GetFiles(legacyPath, "*.rtf")
                                   .ToDictionary(f => Path.GetFileName(f), f => f);

        var pineFiles = Directory.GetFiles(pinePath, "*.rtf")
                                 .ToDictionary(f => Path.GetFileName(f), f => f);

        var commonNames = legacyFiles.Keys.Intersect(pineFiles.Keys).OrderBy(x => x).ToList();

        Console.WriteLine($"Found {commonNames.Count} matching filename pairs.");

        var allLegacyFillpoints = new List<string>();
        var allPineFillpoints   = new List<string>();
        var globalLegacyVars   = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        var globalLegacyFuncs  = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        var globalPineVars    = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        var globalPineFuncs   = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);

        foreach (var baseName in commonNames)
        {
            string legacyFile = legacyFiles[baseName];
            string pineFile   = pineFiles[baseName];

            Console.WriteLine($"\n=== Pair: {baseName} ===");

            var legacyFps = ExtractFillpoints(legacyFile, isLegacy: true);
            Console.WriteLine($"  Legacy: {legacyFps.Count} fillpoints (cleaned)");
            allLegacyFillpoints.AddRange(legacyFps);

            var pineFps = ExtractFillpoints(pineFile, isLegacy: false);
            Console.WriteLine($"  Pine:   {pineFps.Count} fillpoints");
            allPineFillpoints.AddRange(pineFps);

            // Per-pair: top 5 vars and top 5 funcs for legacy and pine
            var legVars = CountVarOccurrences(legacyFps, isLegacy: true);
            var legFuncs = CountFuncOccurrences(legacyFps, isLegacy: true);
            var pineVars = CountVarOccurrences(pineFps, isLegacy: false);
            var pineFuncs = CountFuncOccurrences(pineFps, isLegacy: false);
            foreach (var kv in legVars)   { globalLegacyVars[kv.Key] = globalLegacyVars.GetValueOrDefault(kv.Key, 0) + kv.Value; }
            foreach (var kv in legFuncs)  { globalLegacyFuncs[kv.Key] = globalLegacyFuncs.GetValueOrDefault(kv.Key, 0) + kv.Value; }
            foreach (var kv in pineVars)  { globalPineVars[kv.Key]   = globalPineVars.GetValueOrDefault(kv.Key, 0) + kv.Value; }
            foreach (var kv in pineFuncs){ globalPineFuncs[kv.Key]  = globalPineFuncs.GetValueOrDefault(kv.Key, 0) + kv.Value; }

            Console.WriteLine("  Legacy – top 5 variables: " + string.Join(", ", legVars.OrderByDescending(x => x.Value).Take(5).Select(x => x.Key)));
            Console.WriteLine("  Legacy – top 5 functions: " + string.Join(", ", legFuncs.OrderByDescending(x => x.Value).Take(5).Select(x => x.Key)));
            Console.WriteLine("  Pine – top 5 variables: " + string.Join(", ", pineVars.OrderByDescending(x => x.Value).Take(5).Select(x => x.Key)));
            Console.WriteLine("  Pine – top 5 functions: " + string.Join(", ", pineFuncs.OrderByDescending(x => x.Value).Take(5).Select(x => x.Key)));

            // Sanitize name for folder
            string safeName = Regex.Replace(Path.GetFileNameWithoutExtension(baseName), @"[^a-zA-Z0-9-]", "_");
            string pairOutDir = Path.Combine("..", "output", safeName);

            try
            {
                Directory.CreateDirectory(pairOutDir);
                File.WriteAllText(Path.Combine(pairOutDir, "legacy_cleaned.txt"), string.Join("\n", legacyFps));
                File.WriteAllText(Path.Combine(pairOutDir, "pine.txt"),           string.Join("\n", pineFps));
                Console.WriteLine($"  Saved to output/{safeName}");
            }
            catch (Exception ex)
            {
                Console.WriteLine($"  Error saving pair {baseName}: {ex.Message}");
            }
        }

        // Overall summary
        Console.WriteLine("\n=== Overall Summary ===");
        Console.WriteLine($"Processed {commonNames.Count} pairs");
        Console.WriteLine($"Total unique legacy patterns (cleaned): {allLegacyFillpoints.Distinct().Count()}");
        Console.WriteLine($"Total unique pine patterns: {allPineFillpoints.Distinct().Count()}");

        Console.WriteLine("\nTop 10 legacy fillpoint patterns (cleaned):");
        allLegacyFillpoints.Distinct().OrderBy(x => x).Take(10).ToList().ForEach(Console.WriteLine);

        Console.WriteLine("\nTop 10 pine fillpoint patterns:");
        allPineFillpoints.Distinct().OrderBy(x => x).Take(10).ToList().ForEach(Console.WriteLine);

        // Global top 20 variables and functions
        var top20LegacyVars  = globalLegacyVars .OrderByDescending(x => x.Value).Take(20).Select(x => x.Key).ToList();
        var top20LegacyFuncs = globalLegacyFuncs.OrderByDescending(x => x.Value).Take(20).Select(x => x.Key).ToList();
        var top20PineVars    = globalPineVars  .OrderByDescending(x => x.Value).Take(20).Select(x => x.Key).ToList();
        var top20PineFuncs   = globalPineFuncs .OrderByDescending(x => x.Value).Take(20).Select(x => x.Key).ToList();

        Console.WriteLine("\nTop 20 legacy variables (global):");
        top20LegacyVars.ForEach(x => Console.WriteLine("  " + x));
        Console.WriteLine("\nTop 20 legacy functions (global):");
        top20LegacyFuncs.ForEach(x => Console.WriteLine("  " + x));
        Console.WriteLine("\nTop 20 pine variables (global):");
        top20PineVars.ForEach(x => Console.WriteLine("  " + x));
        Console.WriteLine("\nTop 20 pine functions (global):");
        top20PineFuncs.ForEach(x => Console.WriteLine("  " + x));

        // Save global summary as JSON
        var summary = new Dictionary<string, object>
        {
            ["legacy"] = new Dictionary<string, object>
            {
                ["top20Variables"] = top20LegacyVars,
                ["top20Functions"] = top20LegacyFuncs
            },
            ["pine"] = new Dictionary<string, object>
            {
                ["top20Variables"] = top20PineVars,
                ["top20Functions"] = top20PineFuncs
            }
        };
        string outputDir = Path.Combine("..", "output");
        Directory.CreateDirectory(outputDir);
        string jsonPath = Path.Combine(outputDir, "legacy_pine_summary.json");
        File.WriteAllText(jsonPath, JsonSerializer.Serialize(summary, new JsonSerializerOptions { WriteIndented = true }));
        Console.WriteLine("\nSaved global summary to " + jsonPath);

        // Synonym mapping table for legacy JDA → Pine migration
        var synonymMappings = BuildSynonymMappings();
        var synonymsRoot = new { mappings = synonymMappings };
        string synonymsPath = Path.Combine(outputDir, "synonyms.json");
        File.WriteAllText(synonymsPath, JsonSerializer.Serialize(synonymsRoot, new JsonSerializerOptions { WriteIndented = true, PropertyNamingPolicy = JsonNamingPolicy.CamelCase }));
        Console.WriteLine("Saved synonym mappings to " + synonymsPath);

        Console.WriteLine("\nTop 10 suggested mappings (legacy → pine):");
        foreach (var m in synonymMappings.Take(10))
            Console.WriteLine($"  {m.Legacy} → {m.Pine}  [{m.Confidence}] ({m.Example})");
    }

    sealed class SynonymMapping
    {
        public string Legacy { get; set; } = "";
        public string Pine { get; set; } = "";
        public string Confidence { get; set; } = "";
        public string Example { get; set; } = "";
    }

    static List<SynonymMapping> BuildSynonymMappings()
    {
        // Based on top20 legacy/pine from 294-pair summary; 30–40 mappings, high-frequency vars first
        return new List<SynonymMapping>
        {
            new() { Legacy = "JW_Respondent.FullName", Pine = "Respondent.first.FormatName(F L)", Confidence = "high", Example = "from 294-pair top20 #1" },
            new() { Legacy = "JW_CurrentUser.FullName", Pine = "cu.FormatName(F L)", Confidence = "high", Example = "from 294-pair top20 #2" },
            new() { Legacy = "Cust_Complainant.FullName", Pine = "Complainant.first.FormatName(F L)", Confidence = "high", Example = "from 294-pair top20 #3" },
            new() { Legacy = "JW_CaseDetails.ProsNum", Pine = "ProsNum.first.Number or builtin.CaseID", Confidence = "high", Example = "from 294-pair top20 #4" },
            new() { Legacy = "Cust_OBAAttorney.FullName", Pine = "OBAAttorney.first.FormatName(F L)", Confidence = "high", Example = "from 294-pair top20 #8" },
            new() { Legacy = "Cust_OBAAttorney.FirstName", Pine = "OBAAttorney.first.PersonnelFirstName", Confidence = "high", Example = "from 294-pair top20 #5" },
            new() { Legacy = "Cust_OBAAttorney.LastName", Pine = "OBAAttorney.first.PersonnelLastName", Confidence = "high", Example = "from 294-pair top20 #6" },
            new() { Legacy = "Cust_OBAAttorney.Title", Pine = "OBAAttorney title / role attribute", Confidence = "medium", Example = "from 294-pair top20 #7" },
            new() { Legacy = "Cust_RespondentAtty.FullName", Pine = "Defense.first.FormatName(F L) or Respondent attorney", Confidence = "high", Example = "from 294-pair top20 #9" },
            new() { Legacy = "JW_Respondent.LastName", Pine = "Respondent.first.NameLastName", Confidence = "high", Example = "from 294-pair top20 #10" },
            new() { Legacy = "JW_Atty_Pros_Active.FullName", Pine = "Prosecutor.first.FormatName(F L)", Confidence = "high", Example = "from 294-pair top20 #11" },
            new() { Legacy = "Cust_Complainant.LastName", Pine = "Complainant.first.NameLastName", Confidence = "high", Example = "from 294-pair top20 #12" },
            new() { Legacy = "JW_Respondent.MrMs", Pine = "RespondentInfo.Prefix or Gender", Confidence = "medium", Example = "from 294-pair top20 #13" },
            new() { Legacy = "Cust_Complainant.MrMs", Pine = "Complainant prefix / RespondentInfo-style", Confidence = "medium", Example = "from 294-pair top20 #14" },
            new() { Legacy = "Cust_Complainant.StateIDNum.IsEmpty", Pine = "Complainant.StateIDNum IsNullOrEmpty or .Any", Confidence = "medium", Example = "from 294-pair top20 #15" },
            new() { Legacy = "Cust_Complainant.StateIDNum", Pine = "Complainant.StateIDNum or StateID", Confidence = "medium", Example = "from 294-pair top20 #16" },
            new() { Legacy = "Cust_Complainant_MailAddress.City", Pine = "ComplainantAddress.first.City.SetCasing(Title)", Confidence = "high", Example = "from 294-pair top20 #17" },
            new() { Legacy = "Cust_Complainant_MailAddress.Address", Pine = "ComplainantAddress.first.StreetAddress.SetCasing(Title)", Confidence = "high", Example = "from 294-pair top20 #18" },
            new() { Legacy = "Cust_Complainant_MailAddress.StateCode", Pine = "ComplainantAddress.first.State", Confidence = "high", Example = "from 294-pair top20 #19" },
            new() { Legacy = "Cust_Complainant_MailAddress.Zip", Pine = "ComplainantAddress.first.Zip", Confidence = "high", Example = "from 294-pair top20 #20" },
            new() { Legacy = "KF_Atty_Def_Active.FullName", Pine = "Defense.first.FormatName(F L)", Confidence = "high", Example = "from TAN Dismissal" },
            new() { Legacy = "kf_Atty_Pros_Active.FullName", Pine = "Prosecutor.first.FormatName(F L)", Confidence = "high", Example = "from Subpoena, Stipulation" },
            new() { Legacy = "KF_Atty_Pros_Active.FullName", Pine = "Prosecutor.first.FormatName(F L)", Confidence = "high", Example = "from Subpoena, TAN Dismissal" },
            new() { Legacy = "JW_Defendant.FullName", Pine = "Respondent.first.FormatName(F L)", Confidence = "high", Example = "from TAN Dismissal, Subpoena" },
            new() { Legacy = "kf_Defendant.FullName", Pine = "Respondent.first.FormatName(F L)", Confidence = "high", Example = "from Subpoena" },
            new() { Legacy = "JW_Defendant_Address.StateCode", Pine = "RespondentAddress.first.State", Confidence = "high", Example = "from TAN Dismissal, Subpoena" },
            new() { Legacy = "JW_Defendant_Address.Address", Pine = "RespondentAddress.first.StreetAddress.SetCasing(Title)", Confidence = "high", Example = "from TAN Dismissal" },
            new() { Legacy = "JW_Defendant_Address.City", Pine = "RespondentAddress.first.City.SetCasing(Title)", Confidence = "high", Example = "from TAN Dismissal" },
            new() { Legacy = "JW_Defendant_Address.Zip", Pine = "RespondentAddress.first.Zip", Confidence = "high", Example = "from TAN Dismissal" },
            new() { Legacy = "kf_Atty_Pros_Active_AgencyNum.CaseAgencyNumber", Pine = "ProsNum.first.Number", Confidence = "high", Example = "from Subpoena Investigation" },
            new() { Legacy = "StateCode", Pine = "State", Confidence = "high", Example = "address fields" },
            new() { Legacy = "Address", Pine = "StreetAddress.SetCasing(Title)", Confidence = "high", Example = "address fields" },
            new() { Legacy = "City", Pine = "City.SetCasing(Title)", Confidence = "high", Example = "address fields" },
            new() { Legacy = "Zip", Pine = "Zip", Confidence = "high", Example = "address fields" },
            new() { Legacy = "TitleCase( … )", Pine = "….SetCasing(Title)", Confidence = "high", Example = "294-pair top20 function" },
            new() { Legacy = "LowerCase( … )", Pine = "….SetCasing(Lower)", Confidence = "high", Example = "294-pair top20 function" },
            new() { Legacy = "UpperCase( … )", Pine = "….SetCasing(Upper)", Confidence = "high", Example = "294-pair top20 function" },
            new() { Legacy = "Initials(…)", Pine = "FormatName(FILI)", Confidence = "high", Example = "294-pair top20 function" },
            new() { Legacy = "FormatDate", Pine = "builtin.today.FormatDate or FormatDate", Confidence = "high", Example = "294-pair top20 function" },
            new() { Legacy = "CurrentDate()", Pine = "builtin.today", Confidence = "high", Example = "294-pair top20 function" },
            new() { Legacy = "If", Pine = "If", Confidence = "high", Example = "same in Pine" },
            new() { Legacy = "ElseIf", Pine = "ElseIf", Confidence = "high", Example = "same in Pine" },
            new() { Legacy = "Subdocument", Pine = "SubDocument", Confidence = "high", Example = "294-pair top20 function" },
            new() { Legacy = "MultiSelect", Pine = "Foreach", Confidence = "high", Example = "294-pair top20 function" },
            new() { Legacy = "ForEach", Pine = "Foreach", Confidence = "high", Example = "294-pair Pine" },
            new() { Legacy = ".IsEmpty", Pine = ".Any or IsNullOrEmpty", Confidence = "medium", Example = "e.g. FullName.IsEmpty" },
            new() { Legacy = "Cca", Pine = "Cca", Confidence = "high", Example = "same in Pine" },
        };
    }

    static bool IsHex(char c) => (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');

    static List<string> ExtractFillpoints(string filePath, bool isLegacy)
    {
        string content = File.ReadAllText(filePath);
        var fillpoints = new List<string>();

        if (isLegacy)
        {
            // Find %[ then the matching ] by bracket depth, skipping RTF controls so we don't stop at noise
            int i = 0;
            while (i < content.Length)
            {
                int start = content.IndexOf("%[", i, StringComparison.Ordinal);
                if (start < 0) break;

                int depth = 1;
                int pos = start + 2;  // after "["

                while (pos < content.Length && depth > 0)
                {
                    char c = content[pos];

                    if (c == '\\')
                    {
                        // Skip RTF control: \word, \symbol, or hex escape \'XX
                        pos++;
                        if (pos >= content.Length) break;
                        if (content[pos] == '\'' && pos + 2 < content.Length &&
                            IsHex(content[pos + 1]) && IsHex(content[pos + 2]))
                        {
                            pos += 3;  // skip 'XX (hex escape – don't count possible ] from \'5d)
                            continue;
                        }
                        if (char.IsLetter((char)content[pos]))
                        {
                            while (pos < content.Length && char.IsLetter((char)content[pos])) pos++;
                            if (pos < content.Length && content[pos] == '-') pos++;
                            while (pos < content.Length && char.IsDigit(content[pos])) pos++;
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
                string raw = content.Substring(start, endInclusive - start + 1);
                // Remove any trailing RTF junk after the closing ] (e.g. \par, \cell, groups)
                int closeIndex = raw.Length - 1;
                raw = raw.Substring(0, closeIndex + 1);

                // Remove inner RTF controls but keep fillpoint content: strip \word and \symbol, and standalone { }
                // Do NOT use a pattern that matches {\...} to the next } – that would eat content inside the group
                raw = Regex.Replace(raw, @"\\[^ ]+", " ");
                raw = Regex.Replace(raw, @"\{|\}", " ");

                // Normalize whitespace inside (collapse multiples, keep single spaces between words)
                raw = Regex.Replace(raw, @"\s+", " ").Trim();

                if (raw.StartsWith("%[") && raw.EndsWith("]") && raw.Length > 8)
                {
                    fillpoints.Add(raw);
                }

                i = endInclusive + 1;
            }
        }
        else
        {
            // Pine - simple
            var matches = Regex.Matches(content, @"\@\[.*?\]", RegexOptions.Singleline);
            fillpoints.AddRange(matches.Cast<Match>().Select(m => m.Value.Trim()));
        }

        // Light filter - only drop obvious junk
        return fillpoints
            .Where(fp => fp.Length > 8 && !fp.StartsWith("%[}{"))
            .Distinct()
            .OrderBy(x => x)
            .ToList();
    }

    // Dotted identifiers (e.g. KF_Atty_Def_Active.FullName, x.StreetAddress)
    static readonly Regex VarPattern = new Regex(@"\b[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+\b", RegexOptions.Compiled);
    // Word immediately followed by ( (function call)
    static readonly Regex FuncPattern = new Regex(@"\b([A-Za-z][A-Za-z0-9_]*)\s*\(", RegexOptions.Compiled);

    static HashSet<string> ExtractVariablesFromFillpoints(IEnumerable<string> fillpoints, bool isLegacy)
    {
        var set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var fp in fillpoints)
        {
            string? inner = GetFillpointInner(fp, isLegacy);
            if (string.IsNullOrEmpty(inner)) continue;
            foreach (Match m in VarPattern.Matches(inner))
                set.Add(m.Value);
        }
        return set;
    }

    static HashSet<string> ExtractFunctionsFromFillpoints(IEnumerable<string> fillpoints, bool isLegacy)
    {
        var set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var fp in fillpoints)
        {
            string? inner = GetFillpointInner(fp, isLegacy);
            if (string.IsNullOrEmpty(inner)) continue;
            foreach (Match m in FuncPattern.Matches(inner))
                set.Add(m.Groups[1].Value);
        }
        return set;
    }
z
    static Dictionary<string, int> CountVarOccurrences(IEnumerable<string> fillpoints, bool isLegacy)
    {
        var counts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        foreach (var fp in fillpoints)
        {
            string? inner = GetFillpointInner(fp, isLegacy);
            if (string.IsNullOrEmpty(inner)) continue;
            foreach (Match m in VarPattern.Matches(inner))
            {
                string key = m.Value;
                counts[key] = counts.GetValueOrDefault(key, 0) + 1;
            }
        }
        return counts;
    }

    static Dictionary<string, int> CountFuncOccurrences(IEnumerable<string> fillpoints, bool isLegacy)
    {
        var counts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        foreach (var fp in fillpoints)
        {
            string? inner = GetFillpointInner(fp, isLegacy);
            if (string.IsNullOrEmpty(inner)) continue;
            foreach (Match m in FuncPattern.Matches(inner))
            {
                string key = m.Groups[1].Value;
                counts[key] = counts.GetValueOrDefault(key, 0) + 1;
            }
        }
        return counts;
    }

    static string? GetFillpointInner(string fp, bool isLegacy)
    {
        if (isLegacy && fp.StartsWith("%[") && fp.EndsWith("]")) return fp.Substring(2, fp.Length - 3);
        if (!isLegacy && fp.StartsWith("@[") && fp.EndsWith("]")) return fp.Substring(2, fp.Length - 3);
        return null;
    }
}