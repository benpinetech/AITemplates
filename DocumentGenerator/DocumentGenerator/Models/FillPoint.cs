using DocumentGenerator.Utils;
using System.Text;
using System.Text.RegularExpressions;

namespace DocumentGenerator.Models
{
    public class FillPoint
    {
        public Match MatchInSource { get; }
        public string FullVariable { get; } // e.g., @\b [var]
        public string CleanedVariable { get; } // e.g., [var]
        public string RtfPrefix { get; } // e.g., \b 
        public FillPoint(Match match, TemplateLogger logger)
        {
            MatchInSource = match;
            FullVariable = match.Value;
            RtfPrefix = ExtractRtfPrefix(match.Value, logger);
            CleanedVariable = DocumentTemplateUtil.RemoveRtfMarkup(match.Value);
        }

        private string ExtractRtfPrefix(string input, TemplateLogger logger)
        {
            // Match RTF control words before and within the variable
            var controlMatch = RtfRegularExpressions.ControlWordRegex.Matches(input);
            var prefixBuilder = new StringBuilder();
            foreach (Match match in controlMatch)
            {
                if (match.Groups["controlWords"].Success)
                {
                    prefixBuilder.Append(match.Groups["controlWords"].Value);
                    if (match.Groups["delineator"].Success)
                    {
                        prefixBuilder.Append(match.Groups["delineator"].Value);
                    }
                }
            }
            string prefix = prefixBuilder.ToString();
            return prefix;
        }
    }
}
