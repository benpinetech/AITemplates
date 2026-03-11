using DocumentGenerator.Exceptions;
using PineCone.BuildingBlocks.Data.Constants;
using System.Collections.ObjectModel;
using System.Text;
using System.Text.RegularExpressions;

namespace DocumentGenerator.Utils;

public static class DocumentTemplateUtil
{
    public static class StandaloneFunctions
    {
        public const string CREATE_VAR_FUNC = "@CreateVar";
        public const string GET_AGE = "@GetAge";
        public const string GET_DATE_DIFF = "@GetDateDiff";

        public static readonly List<string> AllowedStandaloneFuncs = new()
        {
            CREATE_VAR_FUNC,
            GET_AGE,
            GET_DATE_DIFF
        };
        public static string GetAge(string birthDateStr, string referenceDateStr)
        {
            if (!DateTime.TryParse(birthDateStr, out DateTime birthDate))
                return "Invalid birth date";

            if (!DateTime.TryParse(referenceDateStr, out DateTime referenceDate))
                return "Invalid reference date";

            int age = referenceDate.Year - birthDate.Year;
            if (referenceDate.Month < birthDate.Month || (referenceDate.Month == birthDate.Month && referenceDate.Day < birthDate.Day))
                age--;

            return age.ToString();
        }

        public static string GetDateDiff(string date1Str, string date2Str, string unit)
        {
            if (!DateTime.TryParse(date1Str, out DateTime date1))
                return "Invalid date1";

            if (!DateTime.TryParse(date2Str, out DateTime date2))
                return "Invalid date2";

            TimeSpan diff = date2 - date1;
            return unit.ToLower() switch
            {
                "y" => (((date2.Year - date1.Year) * 12 + date2.Month - date1.Month - (date2.Day < date1.Day ? 1 : 0)) / 12).ToString(),
                "m" => ((date2.Year - date1.Year) * 12 + date2.Month - date1.Month - (date2.Day < date1.Day ? 1 : 0)).ToString(),
                "d" => diff.TotalDays.ToString("0"),
                "h" => diff.TotalHours.ToString("0"),
                "min" => diff.TotalMinutes.ToString("0"),
                _ => "Invalid unit"
            };
        }
    }

    public static class ObjectFunctions
    {
        public const string FORMAT_NAME = "formatname";
    }

    public static class ChainedFunctions
    {
        public const string FORMAT_DATE_FUNC = "formatdate";
        public const string SET_STRING_CASING = "setcasing";
        public const string GET_LABEL = "getlabel";
        public const string FOR_EACH = "foreach";
        public const string IF = "if";
        public const string ELSE_IF = "elseif";
        public const string ELSE = "else";
        public const string IS_NULL_OR_EMPTY = "isnullorempty";
        public const string FORMAT_NUMBER = "formatnumber";
        public const string FORMAT_PHONE_NUMBER = "formatphonenumber";
        public const string GET_STRING_SEGMENT = "getstringsegment";

        public static readonly List<string> AllowedChainedFuncs = new List<string>()
        {
            FORMAT_DATE_FUNC,
            SET_STRING_CASING,
            GET_LABEL,
            FOR_EACH,
            IS_NULL_OR_EMPTY,
            FORMAT_NUMBER,
            FORMAT_PHONE_NUMBER,
            GET_STRING_SEGMENT
        };
    }

    private static class BuiltInVars
    {
        private const string CaseBuiltInVar = "caseid";
        private const string TodayBuiltInVar = "today";
        private const string CurrentUserUserID = "currentuseruserid";
        private const string CurrentUserPersonnelID = "currentuserpersonnelid";
        private const string PaymentBuiltInVar = "paymentid";

        public static readonly List<string> ListAll = new List<string>()
        {
            CaseBuiltInVar,
            TodayBuiltInVar,
            CurrentUserPersonnelID,
            CurrentUserUserID,
            PaymentBuiltInVar
        };
    }

    public static class CasingOptions
    {
        public const string UPPER_CASE = "upper";
        public const string LOWER_CASE = "lower";
        public const string TITLE_CASE = "title";
        public const string SENTENCE_CASE = "sentence";

        public static readonly List<string> ListAll = new()
        {
            UPPER_CASE,
            LOWER_CASE,
            TITLE_CASE,
            SENTENCE_CASE
        };
    }

    public static class StringSegmentOptions
    {
        public const string FIRST = "first";
        public const string LAST = "last";

        public static readonly List<string> ListAll = new()
        {
            FIRST,
            LAST
        };
    }

    public static class TemplateLogType
    {
        public const string INFO = "Info";
        public const string WARNING = "Warning";
        public const string ERROR = "Error";

        public static readonly List<string> ListAll = new()
        {
            INFO,
            WARNING,
            ERROR
        };
    }

    private static List<string> _entityVarList = new();


    private static readonly List<string> ValidFileContentTypes = new List<string>()
    {
        "application/rtf",
        "text/plain",
        "text/html"
    };

    public static readonly List<string> ValidFileExtensions = new List<string>()
    {
        ".rtf",
        ".txt",
        ".html"
    };

    public static string ExtractTextBetweenParentheses(string input)
    {
        // find first parentheses
        var argsStartIndex = input.IndexOf('(') + 1;
        var argsFinishIndex = -1;
        var openParenCount = 0;
        // loop through string to find matching parenthesis
        for (var i = argsStartIndex; i < input.Length; i++)
        {
            if (input[i] == '(')
                openParenCount++;
            if (input[i] == ')' && openParenCount > 0)
                openParenCount--;
            else if (input[i] == ')')
            {
                argsFinishIndex = i;
                break;
            }
        }

        if (argsFinishIndex < 0)
            throw new PineTemplateGeneratorException(DocumentGeneratorErrorCodes.PARENTHESIS_DONT_MATCH, input);

        // return value between indexes
        return input.Substring(argsStartIndex, argsFinishIndex - argsStartIndex);
    }
    public static bool CheckTemplateContentTypeIsValid(string fileContentType)
    {
        return ValidFileContentTypes.Contains(fileContentType);
    }
    public static bool CheckFileExtensionIsValid(string fileExtension)
    {
        return ValidFileExtensions.Contains(fileExtension);
    }
    public static string EncodeTemplateTextForDatabaseStorage(string decodedString)
    {
        return Convert.ToBase64String(Encoding.UTF8.GetBytes(decodedString));
    }
    public static string DecodeTemplateTextForUsage(string encodedString)
    {
        string decodedString = string.Empty;

        if (IsDoubleBase64(encodedString))
        {
            string firstDecodedStr = Encoding.UTF8.GetString(Convert.FromBase64String(encodedString));
            decodedString = Encoding.UTF8.GetString(Convert.FromBase64String(firstDecodedStr));
        }
        else
        {
            try
            {
                decodedString = Encoding.UTF8.GetString(Convert.FromBase64String(encodedString));
            }
            catch (Exception ex)
            {
                Console.WriteLine(ex);
            }
        }
        return decodedString;
    }
    static bool IsDoubleBase64(string encodedStr)
    {
        try
        {
            // First decode attempt
            byte[] firstDecodedBytes = Convert.FromBase64String(encodedStr);
            string firstDecodedStr = Encoding.UTF8.GetString(firstDecodedBytes);

            // Second decode attempt
            byte[] secondDecodedBytes = Convert.FromBase64String(firstDecodedStr);
            string secondDecodedStr = Encoding.UTF8.GetString(secondDecodedBytes);

            return true; // Successfully decoded twice, so it's double-encoded
        }
        catch
        {
            return false; // Decoding failed at some step, so it's not double Base64
        }
    }
    public static string[] GetCommandParts(string fullCommand)
    {
        var result = new List<string>();
        var parenthesesDepth = 0;
        var lastSplit = 0;

        for (var i = 0; i < fullCommand.Length; i++)
        {
            var currentChar = fullCommand[i];

            switch (currentChar)
            {
                case '(':
                    parenthesesDepth++;
                    break;
                case ')':
                    parenthesesDepth--;
                    break;
                case '.' when parenthesesDepth == 0:
                    result.Add(fullCommand.Substring(lastSplit, i - lastSplit).Trim());
                    lastSplit = i + 1;
                    break;
            }
        }

        // Add the last part of the string
        result.Add(fullCommand[lastSplit..].Trim());

        return result.ToArray();
    }

    public static string RemoveRtfMarkup(string input)
    {
        if (string.IsNullOrEmpty(input))
        {
            return input;
        }
        var sb = new StringBuilder();
        bool inVariable = false;
        int bracketCount = 0;
        bool inVariableName = false;
        for (int i = 0; i < input.Length; i++)
        {
            char c = input[i];
            if (c == '@' && i + 1 < input.Length && input[i + 1] == '[')
            {
                inVariable = true;
                inVariableName = true;
                sb.Append(c);
                continue;
            }
            if (inVariable)
            {
                if (c == '[')
                {
                    bracketCount++;
                    sb.Append(c);
                }
                else if (c == ']')
                {
                    bracketCount--;
                    sb.Append(c);
                    if (bracketCount == 0)
                    {
                        inVariable = false;
                        inVariableName = false;
                    }
                }
                else if (c == '\\')
                {
                    i++;
                    if (i >= input.Length) break;
                    char next = input[i];
                    if (next == '\'' || next == '{' || next == '}' || next == '\\')
                    {
                        if (next == '\'')
                        {
                            if (i + 2 < input.Length)
                            {
                                string hex = input.Substring(i + 1, 2);
                                if (int.TryParse(hex, System.Globalization.NumberStyles.HexNumber, null, out int charCode))
                                {
                                    sb.Append((char)charCode);
                                }
                                i += 2;
                            }
                        }
                        else
                        {
                            sb.Append(next);
                        }
                    }
                    else
                    {
                        // Skip RTF control words
                        while (i < input.Length && char.IsLetter(input[i])) i++;
                        while (i < input.Length && (char.IsDigit(input[i]) || input[i] == '-')) i++;
                        if (i < input.Length && input[i] == ' ') i++;
                        i--;
                    }
                }
                else if (c == '\r' || c == '\n')
                {
                    continue;
                }
                else if (c == '{')
                {
                    int j = i + 1;
                    while (j < input.Length && char.IsWhiteSpace(input[j])) j++;
                    if (j < input.Length && (char.IsDigit(input[j]) || input[j] == '\\'))
                    {
                        continue; // Skip the brace, keep processing
                    }
                }
                else if (c == '}')
                {
                    continue;
                }
                else if (c == '.' || c == '(')
                {
                    inVariableName = false; // End of variable name when hitting a dot or function
                    sb.Append(c);
                }
                else
                {
                    sb.Append(c);
                }
            }
        }
        string result = sb.ToString().Trim();
        return result;
    }

    public static string CleanConditionalContent(string input)
    {
        if (string.IsNullOrEmpty(input))
        {
            return input;
        }

        var sb = new StringBuilder();
        for (int i = 0; i < input.Length; i++)
        {
            char c = input[i];
            if (c == '\\')
            {
                i++;
                if (i >= input.Length) break;
                char next = input[i];
                if (next == '\'' || next == '{' || next == '}' || next == '\\')
                {
                    if (next == '\'')
                    {
                        if (i + 2 < input.Length)
                        {
                            string hex = input.Substring(i + 1, 2);
                            if (int.TryParse(hex, System.Globalization.NumberStyles.HexNumber, null, out int charCode))
                            {
                                sb.Append((char)charCode);
                                i += 2;
                            }
                        }
                    }
                    else
                    {
                        sb.Append(next);
                    }
                }
                else
                {
                    // Skip RTF control word
                    while (i < input.Length && char.IsLetter(input[i])) i++;
                    while (i < input.Length && (char.IsDigit(input[i]) || input[i] == '-')) i++;
                    if (i < input.Length && input[i] == ' ') i++;
                    i--; // Loop will increment i
                }
            }
            else if (c == '{' || c == '}')
            {
                // Skip braces (RTF groups)
                continue;
            }
            else if (c == '\r' || c == '\n')
            {
                // Skip line breaks
                continue;
            }
            else
            {
                sb.Append(c);
            }
        }
        return sb.ToString().Trim();
    }

    //TemplateValidation regexs
    private static readonly Regex _fillpoint = new Regex(@"@\[((?:CreateVar|If|ElseIf|GetAge|GetDateDiff)\((?:[^][]|(?<!@)\[.*?\])*?\)\]|\w+(?:\.\w+)*\])", RegexOptions.IgnoreCase);
    private static readonly Regex _rtfTag = new Regex(@"(?<rtfTag>(\\([a-z]|[0-9])*))");
    private static readonly Regex _rtfClutter = new Regex(@"(?<FillpointClutter>(\{|\}| |\r\n))");
    private static readonly Regex _cleanup = new Regex(@"((\[)|(\(\w+\))|(\.)|(|\\)(?<!(?<rtfTag>(\\([a-z]|[0-9])*)))(\w+)|(\])|(?#Temp CreateVar Searches)(\@)|(\()|(\))|(\'')|(\:)|(\,))+");
    //FillpointValidation regexs
    private static readonly Regex _OpenBrackets = new Regex(@"\[");
    private static readonly Regex _ClosingBrackets = new Regex(@"\]");
    private static readonly Regex _OpenParens = new Regex(@"\(");
    private static readonly Regex _ClosingParens = new Regex(@"\)");

    public class FillpointValidationResults
    {
        public int FillpointNum { get; set; }
        public string Fillpoint { get; set; }
        public bool PassFail { get; set; }
        public string Message { get; set; }
    }

    public static string FillpointValidate(string fillpoint)
    {
        string fillpointValidation = "";

        // Remove nested fillpoints to avoid counting their brackets
        var cleanedFillpoint = Regex.Replace(fillpoint, @"@\[.*?[^@]\]", "");

        MatchCollection openBracket = _OpenBrackets.Matches(cleanedFillpoint);
        MatchCollection closingBracket = _ClosingBrackets.Matches(cleanedFillpoint);
        MatchCollection openParentheses = _OpenParens.Matches(cleanedFillpoint);
        MatchCollection closingParentheses = _ClosingParens.Matches(cleanedFillpoint);

        if (openBracket.Count > closingBracket.Count)
        {
            fillpointValidation += "Fillpoint Missing Closing Bracket, ";
        }

        if (openBracket.Count < closingBracket.Count)
        {
            fillpointValidation += "Fillpoint Missing Opening Bracket, ";
        }

        if (openParentheses.Count > closingParentheses.Count)
        {
            fillpointValidation += "Fillpoint Missing Closing Parentheses, ";
        }

        if (openParentheses.Count < closingParentheses.Count)
        {
            fillpointValidation += "Fillpoint Missing Opening Parentheses, ";
        }

        if (fillpointValidation.Length != 0)
        {
            return fillpointValidation.ToString().TrimEnd(',', ' ');
        }
        else
        {
            return "Valid";
        }
    }

    public static (string decodedText, string TemplateValidationResults, Collection<FillpointValidationResults> fillpointValidationResults) TemplateValidate(string decodedText)
    {
        var result = new StringBuilder(decodedText);

        int fillpointTotal = 0;
        int scatteredFillpoints = 0;
        int validationFailedFillpoints = 0;

        var validationResults = new Collection<FillpointValidationResults>();

        //all found fillpoints
        MatchCollection matches = _fillpoint.Matches(decodedText);
        foreach (Match match in matches)
        {
            string fillpointResult = "";
            //validation
            fillpointResult = FillpointValidate(match.Value);
            if (fillpointResult != "Valid")
            {
                fillpointTotal++;
                validationFailedFillpoints++;
                validationResults.Add(new FillpointValidationResults { FillpointNum = fillpointTotal, Fillpoint = match.Value, PassFail = false, Message = fillpointResult });
            }
            else
            {
                //original value for checking to ensure no lost characters
                string variable = match.Value;
                //face value of fillpoint found
                string fillpointValue = match.Value;
                //holding value for updating original value if no characters are lost post scrub
                string scrubbedVariable = match.Value;

                Match rtfScrub = _rtfTag.Match(scrubbedVariable);
                if (!rtfScrub.Success)
                {
                    fillpointTotal++;
                    validationResults.Add(new FillpointValidationResults { FillpointNum = fillpointTotal, Fillpoint = fillpointValue, PassFail = true, Message = "No RTF Detected" });
                }
                else
                {
                    //cleanup part 1/2 of variable to discern face value of fillpoint
                    //scrub found variable of rtf tags
                    while (rtfScrub.Success)
                    {
                        fillpointValue = Regex.Replace(fillpointValue, Regex.Escape(rtfScrub.Value), "");

                        rtfScrub = rtfScrub.NextMatch();
                    }

                    //cleanup part 2/2 of variable to discern face value of fillpoint
                    //scrub found fillpoint of rtf clutter
                    Match clutter = _rtfClutter.Match(fillpointValue);
                    while (clutter.Success)
                    {
                        fillpointValue = Regex.Replace(fillpointValue, Regex.Escape(clutter.Value), "");

                        clutter = clutter.NextMatch();
                    }

                    //clean up scattered fillpoint
                    Match cleanup = _cleanup.Match(variable);
                    while (cleanup.Success)
                    {
                        if (!cleanup.Value.StartsWith("\\"))
                        {
                            scrubbedVariable = Regex.Replace(scrubbedVariable, Regex.Escape(cleanup.Value), "");
                        }
                        cleanup = cleanup.NextMatch();
                    }

                    //add back fillpoint face value to cleaned variable at @ symbol
                    scrubbedVariable = scrubbedVariable.Insert(0, fillpointValue);

                    if (variable.Length != scrubbedVariable.Length)
                    {
                        validationResults.Add(new FillpointValidationResults { FillpointNum = fillpointTotal, Fillpoint = fillpointValue, PassFail = false, Message = "RTF Detected, Scrub Failed (Length Mismatch)" });
                    }
                    else
                    {
                        result.Replace(variable, scrubbedVariable, match.Index, match.Length);
                        fillpointTotal++;
                        scatteredFillpoints++;
                        validationResults.Add(new FillpointValidationResults { FillpointNum = fillpointTotal, Fillpoint = fillpointValue, PassFail = true, Message = "RTF Detected, Scrub Successful" });
                    }
                }
            }
        }

        if (result.Length != decodedText.Length)
        {
            //scrubbing failed, return template text unaltered           
            return (decodedText, "Template Length Check failed, template returned unaltered", validationResults);
        }
        else
        {
            //validation successful, return validated template
            return (result.ToString(), $"Fillpoints scrubbed: {scatteredFillpoints}, Fillpoints that failed validation: {validationFailedFillpoints}, Total Fillpoints found: {fillpointTotal}", validationResults);
        }

    }

    public static string RemoveSkippableGroups(string rtf)
    {
        if (string.IsNullOrEmpty(rtf)) return rtf;

        StringBuilder output = new StringBuilder();
        int i = 0;
        while (i < rtf.Length)
        {
            if (rtf[i] == '{')
            {
                int start = i;
                i++;
                // Skip whitespace after {
                while (i < rtf.Length && char.IsWhiteSpace(rtf[i])) i++;

                if (i + 1 < rtf.Length && rtf[i] == '\\' && rtf[i + 1] == '*')
                {
                    // Skippable group: skip to matching }
                    int level = 1;
                    i += 2; // Skip \*
                    while (i < rtf.Length && level > 0)
                    {
                        if (rtf[i] == '{') level++;
                        else if (rtf[i] == '}') level--;
                        i++;
                    }
                }
                else
                {
                    // Not skippable: append from start to current i
                    output.Append(rtf.Substring(start, i - start));
                }
            }
            else
            {
                output.Append(rtf[i]);
                i++;
            }
        }
        return output.ToString();
    }
}