using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using AiInCourtAssistant.Server.Services;

namespace AiInCourtAssistant.Server.Features.TemplateMigration;

/// <summary>Generates Pine template text from a natural language description using Grok/OpenAI-compatible API.</summary>
public sealed class TemplateGenerateService
{
    private readonly IWebHostEnvironment _env;
    private readonly IConfiguration _cfg;
    private readonly ISecretStore? _secrets;
    private readonly MigrationService _migration;
    private readonly ILogger<TemplateGenerateService> _log;
    private static readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(90) };

    /// <summary>Legacy JDA fillpoint blocks from TAN Dismissal Letter (style reference for structure and fillpoint placement).</summary>
    private static readonly string DefaultLegacyStyleBlocks = @"%[ Subdocument(Template\Letterhead) ]
%[ FormatDate (CurrentDate(),MMMM d, yyyy) ]
%[ If ( KF_Atty_Def_Active.FullName.IsEmpty) ]
%[ MultiSelect(a in JW_Defendant_Address) ]
%[ TitleCase( JW_Defendant.FullName) ]
%[ If(JW_Defendant_Address.AddressTypeCode=RBA) ]%[ KF_Def_NameAttribute.NameAttributeValue ]
%[ EndIf ]%[ TitleCase( JW_Defendant_Address.Address) ]
%[ TitleCase( JW_Defendant_Address.City) ], %[ JW_Defendant_Address.StateCode ] %[ JW_Defendant_Address.Zip ]
%[ EndMultiSelect ]%[ Else ]
%[ TitleCase( JW_Defendant.FullName) ]
c/o %[ TitleCase( KF_Atty_Def_Active.FullName) ], Esq.
%[ If(KF_Atty_Def_Active_Address.AddressTypeCode=RBA) ]%[ KF_NameAttribute.NameAttributeValue ]
%[ EndIf ]%[ TitleCase( KF_Atty_Def_Active_Address.Address) ]
%[ TitleCase( KF_Atty_Def_Active_Address.City) ], %[ KF_Atty_Def_Active_Address.StateCode ] %[ KF_Atty_Def_Active_Address.Zip ]
%[ EndIf ]";

    /// <summary>Full legacy structure example from TAN Dismissal Letter: body and signature (convert fillpoints to Pine in output).</summary>
    private static readonly string LegacyStructureExample = @"Notices of Overdraft in Trust Account
Case Numbers: %[kf_Atty_Pros_Active_AgencyNum.CaseAgencyNumber]
Dear %[If(KF_Atty_Def_Active.FullName.IsEmpty)]%[JW_Defendant.MrMs] %[TitleCase(JW_Defendant.LastName)]%[Else]%[KF_Atty_Def_Active.MrMs] %[TitleCase(KF_Atty_Def_Active.LastName)]%[EndIf]:
The Office of Attorney Regulation Counsel investigated the above-referenced matters pursuant to the Colorado Rules of Procedure Regarding Attorney Discipline and Disability Proceedings. We determined the circumstances herein do not violate the Colorado Rules of Professional Conduct.
Thank you for your cooperation and timely assistance in the investigation of these matters.
These matters are dismissed.
Sincerely,
%[JW_Atty_Pros_Active.FullName]
%[KF_AttyProsTitle.NameAttributeValue]
%[Initials(JW_Atty_Pros_Active.FullName, false)]/%[LowerCase(Initials(KF_Investigator.FullName, false))]";

    public TemplateGenerateService(
        IWebHostEnvironment env,
        IConfiguration cfg,
        MigrationService migration,
        ILogger<TemplateGenerateService> log,
        ISecretStore? secrets = null)
    {
        _env = env;
        _cfg = cfg;
        _migration = migration;
        _log = log;
        _secrets = secrets;
    }

    /// <summary>Load few-shot Pine RTF examples: Sebastian.txt, Butler.txt, Cascade.txt, Lyon.txt first (real Pine style), then template_gen_full_rtf_examples.txt. All examples must show only @[...] fillpoints.</summary>
    private string LoadPineRtfFewShotExamples()
    {
        var appData = Path.Combine(_env.ContentRootPath, "App_Data");
        var sb = new StringBuilder(12000);

        foreach (var name in new[] { "Sebastian.txt", "Butler.txt", "Cascade.txt", "Lyon.txt" })
        {
            var path = Path.Combine(appData, name);
            if (!File.Exists(path)) path = Path.Combine(appData, "pine_fewshot", name);
            if (File.Exists(path))
            {
                var s = File.ReadAllText(path);
                if (s.Length > 5000) s = s.Substring(0, 5000) + "\n... [truncated]";
                sb.Append("\n\n--- ").Append(name).Append(" (Pine @[...] only) ---\n").Append(s);
            }
        }

        var mainPath = Path.Combine(appData, "template_gen_full_rtf_examples.txt");
        if (File.Exists(mainPath))
        {
            var s = File.ReadAllText(mainPath);
            if (s.Length > 8000) s = s.Substring(0, 8000) + "\n... [truncated]";
            if (sb.Length > 0) sb.Append("\n\n");
            sb.Append(s);
        }

        if (sb.Length == 0)
            sb.Append("(No few-shot file found. Generate full RTF with {\\rtf1, \\par paragraphs, and only @[ ... ] fillpoints from synonyms.json. Never use %[ or legacy JW_/KF_/Cust_.)");
        return sb.ToString();
    }

    /// <summary>Generate Pine template text. Returns (generatedText, null) on success or (null, errorMessage) on failure.</summary>
    public async Task<(string? GeneratedText, string? Error)> GenerateAsync(
        string description,
        bool useLegacyAsExample,
        string? legacyContent,
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(description) && !useLegacyAsExample)
            return (null, "Description is required, or use 'Generate from Legacy' with uploaded content.");

        var apiKey = _cfg["Ai:ApiKey"];
        if (string.IsNullOrWhiteSpace(apiKey) && _secrets != null)
            apiKey = await _secrets.GetAsync("OpenAI:ApiKey", ct);
        if (string.IsNullOrWhiteSpace(apiKey))
            return (null, "AI API key is not configured. Set Ai:ApiKey in appsettings or store OpenAI:ApiKey in secrets.");

        var endpoint = _cfg["Ai:Endpoint"];
        if (string.IsNullOrWhiteSpace(endpoint))
            endpoint = "https://api.openai.com/v1/chat/completions";
        var model = _cfg["Ai:Model"] ?? "gpt-4o-mini";

        string legacyStyleBlocks = DefaultLegacyStyleBlocks;
        if (useLegacyAsExample && !string.IsNullOrWhiteSpace(legacyContent))
        {
            var fillpoints = MigrationService.ExtractFillpointsFromRtf(legacyContent);
            if (fillpoints.Count > 0)
                legacyStyleBlocks = string.Join("\n", fillpoints.Take(40));
            else if (legacyContent.TrimStart('\uFEFF').Length <= 3000)
                legacyStyleBlocks = legacyContent.TrimStart('\uFEFF');
            else
                legacyStyleBlocks = legacyContent.TrimStart('\uFEFF').Substring(0, 3000) + "\n...";
        }

        string pineRtfFewShot = LoadPineRtfFewShotExamples();

        var mappings = _migration.GetMappings();
        var mappingsJson = JsonSerializer.Serialize(mappings, new JsonSerializerOptions { WriteIndented = false });

        var userPrompt = $@"You are a legal document automation expert converting to Pine format. Generate a complete Pine template (full RTF text with only @[...] fillpoints) for the following description: {description}

Use these legacy JDA examples as style reference only (convert all fillpoints to Pine — do not output any %[...] or legacy syntax):
---
{legacyStyleBlocks}
---

Follow this exact legacy structure example (convert fillpoints to Pine). Use this body/signature structure and phrasing style — do not replace with generic placeholders:
---
{LegacyStructureExample}
---

Apply only these mappings from synonyms.json. Use ONLY these Pine names and functions; do not invent new variables or use legacy prefixes (JW_, KF_, Cust_):
{mappingsJson}

Output only the generated Pine RTF text (no explanations, no markdown fences, no legacy syntax).
Example output style (from real Pine templates — note only @[...] fillpoints, no %[...]):
---
{pineRtfFewShot}
---

STRICT RULES:
- Base the body text, paragraphs, and language on the legacy examples provided — do not use generic placeholders like ""Body paragraph with fillpoints embedded"" or ""Body paragraph"". Use specific, realistic wording and the exact phrasing/structure from the examples where possible. Adapt the substance to the requested document type (e.g. subpoena, letter) but keep the same level of detail and formality.
- Make the generated document detailed and realistic, like a full 1-page letter with letterhead, body, and signature. No placeholder sentences.
- 100% Pine syntax only: every fillpoint MUST use @[ ... ]. NEVER use %[ ... ] or any legacy JDA syntax.
- Use ONLY variable/function names that appear in the mappings JSON above. Do not invent JW_, KF_, Cust_, TitleCase(, FormatDate(CurrentDate(), etc.
- Preserve legacy document structure and formatting (letterhead, date, addresses, body, signature) but express every dynamic part with Pine @[ ... ] from the mappings.
- Output full RTF: start with {{\rtf1, include \\par paragraphs, headings, body text with @[ ... ] embedded. No malformed parentheses; close every @[ with ]. Do not output a list of fillpoints — output the complete RTF document.";

        var payload = new
        {
            model,
            temperature = 0.2,
            max_tokens = 8192,
            messages = new object[]
            {
                new { role = "system", content = "You are a legal document automation expert. You MUST output a single complete RTF document using ONLY Pine fillpoint syntax @[ ... ]. FORBIDDEN: %[ ... ], JW_, KF_, Cust_, or any legacy JDA names; FORBIDDEN: generic placeholder body text like 'Body paragraph with fillpoints embedded'. Use specific, realistic body text based on the legacy examples (e.g. 'The Office of Attorney Regulation Counsel investigated...' style). Use only Pine names from the provided synonyms.json mappings. Start with {\\rtf1, include \\par, letterhead, date, addresses, detailed body, and signature. Output only raw RTF text. No explanations, no markdown, no code fences." },
                new { role = "user", content = userPrompt }
            }
        };

        try
        {
            using var req = new HttpRequestMessage(HttpMethod.Post, endpoint);
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
            req.Content = new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json");

            using var resp = await _http.SendAsync(req, ct);
            var raw = await resp.Content.ReadAsStringAsync(ct);

            if (!resp.IsSuccessStatusCode)
            {
                _log.LogWarning("Template generate API error: {Status} {Body}", (int)resp.StatusCode, raw.Length > 500 ? raw.Substring(0, 500) : raw);
                return (null, $"API error {(int)resp.StatusCode}: {(raw.Length > 200 ? raw.Substring(0, 200) + "…" : raw)}");
            }

            using var doc = JsonDocument.Parse(raw);
            var content = doc.RootElement
                .GetProperty("choices")[0]
                .GetProperty("message")
                .GetProperty("content")
                .GetString();

            if (string.IsNullOrWhiteSpace(content))
                return (null, "API returned empty content.");

            content = content.Trim();
            if (content.StartsWith("```")) content = Regex.Replace(content, @"^```\w*\n?", "").Trim();
            if (content.EndsWith("```")) content = content.Replace("```", "").Trim();
            return (content, null);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Template generate failed");
            return (null, "Generation failed: " + ex.Message);
        }
    }

    /// <summary>AI-assisted fillpoint insertion: suggest where to insert @[...] fillpoints in existing form text. Returns (suggestedText, changes, null) or (null, null, error).</summary>
    public async Task<(string? SuggestedText, IReadOnlyList<string>? Changes, string? Error)> SuggestFillpointsAsync(string? content, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(content))
            return (null, null, "Content is required. Upload an RTF/Word file or paste the form text.");

        var apiKey = _cfg["Ai:ApiKey"];
        if (string.IsNullOrWhiteSpace(apiKey) && _secrets != null)
            apiKey = await _secrets.GetAsync("OpenAI:ApiKey", ct);
        if (string.IsNullOrWhiteSpace(apiKey))
            return (null, null, "AI API key is not configured. Set Ai:ApiKey in appsettings or store OpenAI:ApiKey in secrets.");

        var endpoint = _cfg["Ai:Endpoint"];
        if (string.IsNullOrWhiteSpace(endpoint))
            endpoint = "https://api.openai.com/v1/chat/completions";
        var model = _cfg["Ai:Model"] ?? "gpt-4o-mini";

        content = content.TrimStart('\uFEFF').Trim();
        if (content.Length > 12000)
            content = content.Substring(0, 12000) + "\n\n[... document truncated for API ...]";

        var mappings = _migration.GetMappings();
        var mappingsJson = JsonSerializer.Serialize(mappings, new JsonSerializerOptions { WriteIndented = false });

        var userPrompt = $@"You are a legal document automation expert. Here is an existing criminal justice template (RTF/Word text):

---INPUT---
{content}
---END INPUT---

Analyze the document and suggest where dynamic fillpoints should be inserted using Pine syntax (@[...]).
Use these mappings from synonyms.json as reference for Pine fillpoint names:
{mappingsJson}

Replace static names, dates, addresses, case numbers, etc., with appropriate @[...] fillpoints (e.g. @[Defense.first.FormatName(F L).SetCasing(Title)], @[builtin.today.FormatDate(preset1)], @[ProsNum.first.Number], @[DefendantAddress.first.StreetAddress.SetCasing(Title)]).
Preserve all formatting and non-dynamic text (including RTF controls if present).
Only insert fillpoints where it makes sense (names, dates, addresses, case info, conditionals, loops). If uncertain, leave as static text.

Output in this exact format (no other text before or after):
---DOCUMENT---
[full modified RTF/Word text with fillpoints inserted]
---CHANGES---
- Replaced '...' with @[...]
- Replaced '...' with @[...]
[one line per change, or write ""No replacements"" if none]";

        var payload = new
        {
            model,
            temperature = 0.2,
            max_tokens = 4096,
            messages = new object[]
            {
                new { role = "system", content = "You are a legal document automation expert. Output only in the requested format: ---DOCUMENT--- section then ---CHANGES--- section. No other commentary." },
                new { role = "user", content = userPrompt }
            }
        };

        try
        {
            using var req = new HttpRequestMessage(HttpMethod.Post, endpoint);
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
            req.Content = new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json");

            using var resp = await _http.SendAsync(req, ct);
            var raw = await resp.Content.ReadAsStringAsync(ct);

            if (!resp.IsSuccessStatusCode)
            {
                _log.LogWarning("Suggest fillpoints API error: {Status}", (int)resp.StatusCode);
                return (null, null, $"API error {(int)resp.StatusCode}: {(raw.Length > 200 ? raw.Substring(0, 200) + "…" : raw)}");
            }

            using var doc = JsonDocument.Parse(raw);
            var contentResp = doc.RootElement
                .GetProperty("choices")[0]
                .GetProperty("message")
                .GetProperty("content")
                .GetString();

            if (string.IsNullOrWhiteSpace(contentResp))
                return (null, null, "API returned empty content.");

            contentResp = contentResp.Trim();
            string suggestedText;
            var changes = new List<string>();

            var docMarker = "---DOCUMENT---";
            var chgMarker = "---CHANGES---";
            int docStart = contentResp.IndexOf(docMarker, StringComparison.OrdinalIgnoreCase);
            int chgStart = contentResp.IndexOf(chgMarker, StringComparison.OrdinalIgnoreCase);

            if (docStart >= 0 && chgStart > docStart)
            {
                suggestedText = contentResp.Substring(docStart + docMarker.Length, chgStart - docStart - docMarker.Length).Trim();
                var changesBlock = contentResp.Substring(chgStart + chgMarker.Length).Trim();
                foreach (var line in changesBlock.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries))
                {
                    var t = line.Trim();
                    if (t.StartsWith("-")) changes.Add(t.TrimStart('-').Trim());
                    else if (!string.IsNullOrWhiteSpace(t) && !t.Equals("No replacements", StringComparison.OrdinalIgnoreCase)) changes.Add(t);
                }
            }
            else if (docStart >= 0)
            {
                suggestedText = contentResp.Substring(docStart + docMarker.Length).Trim();
                if (suggestedText.StartsWith("```")) suggestedText = Regex.Replace(suggestedText, @"^```\w*\n?", "").Trim();
                if (suggestedText.EndsWith("```")) suggestedText = suggestedText.Replace("```", "").Trim();
            }
            else
            {
                suggestedText = contentResp;
                if (suggestedText.StartsWith("```")) suggestedText = Regex.Replace(suggestedText, @"^```\w*\n?", "").Trim();
                if (suggestedText.EndsWith("```")) suggestedText = suggestedText.Replace("```", "").Trim();
            }

            return (suggestedText, changes, null);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Suggest fillpoints failed");
            return (null, null, "Suggest fillpoints failed: " + ex.Message);
        }
    }
}
