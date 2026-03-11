using System.Text;
using System.Text.RegularExpressions;
using RtfPipe;

namespace AiInCourtAssistant.Server.Features.TemplateMigration;

/// <summary>Converts RTF to HTML and highlights fillpoints (%[ ... ] and @[ ... ]) with yellow background.</summary>
public sealed class RtfToHtmlService
{
    private static bool _encodingRegistered;

    /// <summary>Convert RTF to HTML; wrap fillpoints in &lt;mark&gt;. Returns (html, null) on success or (null, errorMessage) on failure. On RTF failure, falls back to plain text with fillpoint highlighting.</summary>
    public (string? Html, string? Error) RtfToHtmlWithHighlight(string? rtfOrPlain)
    {
        if (string.IsNullOrEmpty(rtfOrPlain))
            return ("", null);

        RegisterEncoding();

        string content = rtfOrPlain.TrimStart('\uFEFF').Trim();
        bool looksRtf = content.StartsWith("{\\rtf", StringComparison.OrdinalIgnoreCase);

        string html;
        if (looksRtf)
        {
            try
            {
                html = Rtf.ToHtml(content);
                if (string.IsNullOrWhiteSpace(html))
                    html = PlainFallback(content);
            }
            catch (Exception)
            {
                // Fall back to plain text with fillpoint highlighting so user doesn't see raw RTF
                html = PlainFallback(content);
            }
        }
        else
        {
            html = PlainFallback(content);
        }

        html = HighlightFillpointsInHtml(html);
        html = HighlightReviewInHtml(html);
        return (html, null);
    }

    private static string PlainFallback(string content)
    {
        return "<div class=\"migration-plain\">" + EscapeHtml(content) + "</div>";
    }

    /// <summary>Plain text to HTML with fillpoint/review highlighting only (no RTF parsing). Use when RtfToHtmlWithHighlight fails.</summary>
    public static string PlainToHtmlWithHighlight(string? content)
    {
        if (string.IsNullOrEmpty(content)) return "";
        string html = "<div class=\"migration-plain\">" + EscapeHtml(content.TrimStart('\uFEFF').Trim()) + "</div>";
        html = HighlightFillpointsInHtml(html);
        html = HighlightReviewInHtml(html);
        return html;
    }

    /// <summary>Wrap // REVIEW: no mapping for ... in &lt;span class="review-highlight"&gt; for red display.</summary>
    public static string HighlightReviewInHtml(string html)
    {
        if (string.IsNullOrEmpty(html)) return html;
        return Regex.Replace(html, @"(//\s*REVIEW:[^<]*)", "<span class=\"review-highlight\">$1</span>");
    }

    /// <summary>Highlight %[ ... ] and @[ ... ] in already-HTML string (bracket-aware).</summary>
    public static string HighlightFillpointsInHtml(string html)
    {
        if (string.IsNullOrEmpty(html)) return html;

        // Replace %[ ... ] with <mark class="fillpoint-legacy">...</mark> and @[ ... ] with <mark class="fillpoint-pine">...</mark>
        // Use bracket depth so we don't break on ] inside the fillpoint
        var sb = new StringBuilder(html.Length * 2);
        int i = 0;
        while (i < html.Length)
        {
            if (i + 2 <= html.Length && html.Substring(i, 2) == "%[")
            {
                int end = FindMatchingBracket(html, i + 1);
                if (end >= 0)
                {
                    string segment = html.Substring(i, end - i + 1);
                    sb.Append("<mark class=\"fillpoint-legacy\">").Append(segment).Append("</mark>");
                    i = end + 1;
                    continue;
                }
            }
            if (i + 2 <= html.Length && html.Substring(i, 2) == "@[")
            {
                int end = FindMatchingBracket(html, i + 1);
                if (end >= 0)
                {
                    string segment = html.Substring(i, end - i + 1);
                    sb.Append("<mark class=\"fillpoint-pine\">").Append(segment).Append("</mark>");
                    i = end + 1;
                    continue;
                }
            }
            sb.Append(html[i]);
            i++;
        }
        return sb.ToString();
    }

    private static int FindMatchingBracket(string s, int openIndex)
    {
        if (openIndex >= s.Length || s[openIndex] != '[') return -1;
        int depth = 1;
        for (int i = openIndex + 1; i < s.Length; i++)
        {
            if (s[i] == '[') depth++;
            else if (s[i] == ']')
            {
                depth--;
                if (depth == 0) return i;
            }
        }
        return -1;
    }

    private static string EscapeHtml(string text)
    {
        return text
            .Replace("&", "&amp;")
            .Replace("<", "&lt;")
            .Replace(">", "&gt;")
            .Replace("\"", "&quot;");
    }

    private static void RegisterEncoding()
    {
        if (_encodingRegistered) return;
        Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
        _encodingRegistered = true;
    }
}
