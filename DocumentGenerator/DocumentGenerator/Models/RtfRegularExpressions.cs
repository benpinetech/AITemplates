using System.Text.RegularExpressions;

namespace DocumentGenerator.Models
{
    public static partial class RtfRegularExpressions
    {

        // ——————————————————————————————————————————————
        // Source-Generated Regexes (Best practice .NET 7+)
        // ——————————————————————————————————————————————

        [GeneratedRegex(@"@(?:\\[^[\]]+)*\[[^[\]]*(?:\[[^[\]]*])*[^][]*\]", RegexOptions.IgnoreCase)]
        private static partial Regex RtfFillPointRegexGenerated();

        [GeneratedRegex(@"@\\s*\\[[^]@[]+]", RegexOptions.Compiled)]
        private static partial Regex TextOnlyFillPointRegexGenerated();

        [GeneratedRegex(@"(\\([a-z]|[0-9])* )", RegexOptions.Compiled)]
        private static partial Regex RtfTagRegexGenerated();

        [GeneratedRegex(@"{[^{}]*(((?<Open>{)[^{}]*)+((?<Close-Open>})[^{}]*)+)*(?(Open)(?!))}")]
        private static partial Regex TableItemGenerated();
        //public static readonly Regex TableItem = new Regex("{[^{}]*(((?<Open>{)[^{}]*)+((?<Close-Open>})[^{}]*)+)*(?(Open)(?!))}");
        [GeneratedRegex(@"{\\pict[^}]+?(?:\{[^}]+\})*}", RegexOptions.IgnoreCase | RegexOptions.Multiline)]
        private static partial Regex PictureRegexGenerated();

        [GeneratedRegex("\\\\f[0-9]+")]
        private static partial Regex FontGenerated();

        [GeneratedRegex("\\\\f[0-9]+(\\s|\\\\)", RegexOptions.Compiled | RegexOptions.RightToLeft)]
        private static partial Regex TerminatedFontGenerated();

        [GeneratedRegex("\\\\fonttbl\\s*[^{}\\\\]*{[^{}]*(((?<Open>{)[^{}]*)+((?<Close-Open>})[^{}]*)+)*(?(Open)(?!))}+")]
        private static partial Regex FontTableGenerated();

        [GeneratedRegex("\\\\colortbl[^}]+")]
        private static partial Regex ColorTableGenerated();

        [GeneratedRegex("(\\\\highlight[0-9]+)|(\\\\cb[0-9]+)|(\\\\cf[0-9]+)|(\\\\clcbpat[0-9]+)|(\\\\clcbpatraw[0-9]+)|(\\\\brdrcf[0-9]+)")]
        private static partial Regex DocumentColorsGenerated();

        [GeneratedRegex(@"(?:\\[^\\{}\s]+(?:\s*\\[^\\{}\s]+)*)?\s*@\s*\[\s*(?<Tag>if|elseif|else|endif)\s*(?:\((?<Content>[^()]*(\([^()]*\))*)\))?\s*\]",
            RegexOptions.IgnoreCase | RegexOptions.Compiled)]
        private static partial Regex IfElseIfPatternGenerated();

        [GeneratedRegex(@"@\[(?<Tag>if|elseif|else|endif)(?:\((?<Content>(?:[^()]+|(?<Open>\()|(?<-Open>\)))+)\))?\]", RegexOptions.IgnoreCase)]
        private static partial Regex IfElseIfGenerated();

        [GeneratedRegex(@"('[a-zA-Z0-9]+')\s+IN\s+((?:'[a-zA-Z0-9]+'(?:\s*,\s*'[a-zA-Z0-9]+')*))", RegexOptions.IgnoreCase | RegexOptions.Compiled)]
        private static partial Regex IfInPatternGenerated();

        // ——————————————————————————————————————————————
        // Public Accessors (return the generated regex)
        // ——————————————————————————————————————————————

        public static Regex RtfFillPointRegex => RtfFillPointRegexGenerated();
        public static Regex TextOnlyFillPointRegex => TextOnlyFillPointRegexGenerated();
        public static Regex ControlWordRegex => RtfTagRegexGenerated(); // or keep your existing one if preferred
        public static Regex PictureRegex => PictureRegexGenerated();
        public static Regex TableItem => TableItemGenerated();
        public static Regex Font => FontGenerated();
        public static Regex TerminatedFont => TerminatedFontGenerated();
        public static Regex FontTable => FontTableGenerated();
        public static Regex ColorTable => ColorTableGenerated();
        public static Regex DocumentColors => DocumentColorsGenerated();
        public static Regex IfElseIfPattern => IfElseIfPatternGenerated();
        public static Regex IfElseIf => IfElseIfGenerated();
        public static Regex IfInPattern => IfInPatternGenerated();

        public static readonly Regex Unicode = new("[^\\u0000-\\u007F]", RegexOptions.Compiled);
        public static readonly Regex NumberCheck = new("^[-+]?[0-9]+[.]?[0-9]*([eE][-+]?[0-9]+)?$", RegexOptions.Compiled);

        //keeping this group until comfortale with the update

        //public const string CONTROL_WORDS_GROUP = "controlWords";
        //public const string DELINEATOR_GROUP = "delineator";
        //public static readonly Regex TextOnlyFillPointRegex = new Regex("@\\s*\\[[^]@[]+]", RegexOptions.Compiled);
        //[GeneratedRegex(@"@(?:\\[^{}[\]]+)*\[[^{}[\]]*(?:\[[^{}[\]]*])*[^][]*\]", RegexOptions.IgnoreCase)]
        //private static partial Regex RtfFillPointRegex();
        //public static readonly Regex ControlWordRegex = new Regex(@"((\\\\\\\\)|([^\\\\]))(?<controlWords>(\\\\[^\\\\\\s]+)+)(?:(?<delineator> )|(\\\\\\\\))?", RegexOptions.Compiled);
        //public static readonly Regex AtVariable = new Regex("\\@[a-zA-Z0-9_]+", RegexOptions.Compiled);
        ////formatting
        //public static readonly Regex Alignment = new Regex("\\\\q(([cjlrdt])|k[0-9]+)(\\s|\\\\)", RegexOptions.Compiled | RegexOptions.RightToLeft);
        //public static readonly Regex TableItem = new Regex("{[^{}]*(((?<Open>{)[^{}]*)+((?<Close-Open>})[^{}]*)+)*(?(Open)(?!))}");
        //public static readonly Regex PictureRegex = new Regex(@"{\\pict[^}]+?(?:\{[^}]+\})*}", RegexOptions.IgnoreCase | RegexOptions.Multiline);
        //public static readonly Regex Font = new Regex("\\\\f[0-9]+");
        //public static readonly Regex TerminatedFont = new Regex("\\\\f[0-9]+(\\s|\\\\)", RegexOptions.Compiled | RegexOptions.RightToLeft);
        //public static readonly Regex FontTable = new Regex("\\\\fonttbl(\\s*[^{}\\\\]*{[^{}]*(((?<Open>{)[^{}]*)+((?<Close-Open>})[^{}]*)+)*(?(Open)(?!))})+");
        //public static readonly Regex ColorTable = new Regex("\\\\colortbl[^}]+");
        //public static readonly Regex DocumentColors = new Regex("(\\\\highlight[0-9]+)|(\\\\cb[0-9]+)|(\\\\cf[0-9]+)|(\\\\clcbpat[0-9]+)|(\\\\clcbpatraw[0-9]+)|(\\\\brdrcf[0-9]+)");

        //////loops and logic
        ////public static readonly Regex IsFirstLast = new Regex("([a-zA-Z0-9_-]+\\.){2,4}Is(((Fir|La)st)|Empty)(\\s?!?=\\s?(([tT][rR][uU][eE])|([fF][aA][lL][sS][eE])))?");
        ////public static readonly Regex IsFirstLastOrLoopIndex = new Regex("[^\\(|&|[]+\\.[^]]*(Is(Fir|La)st|LoopIndex)", RegexOptions.Compiled);

        ////public static readonly Regex ForEach = new Regex("ForEach\\(\\s*(?<row>[_a-zA-Z0-9]+) [iI][nN] (?<table>[_a-zA-Z0-9\\.]+)\\)", RegexOptions.Compiled);
        ////public static readonly Regex ForEachLoopTag = new Regex("@\\[ForEach\\((?<body>[_a-zA-Z0-9 .]+)\\)\\]", RegexOptions.Compiled);

        //// Replace the existing IfElseIfPattern with this:
        //public static readonly Regex IfElseIfPattern = new Regex(@"(?:\\[^\\{}\s]+(?:\s*\\[^\\{}\s]+)*)?\s*@\s*\[\s*(?<Tag>if|elseIf|Else|else|endif)\s*(?:\((?<Content>[^()]*(\([^()]*\))*)\))?\s*\]",RegexOptions.IgnoreCase | RegexOptions.Compiled,TimeSpan.FromSeconds(5));
        //public static readonly Regex IfElseIf = new Regex(@"@\[(?<Tag>if|elseIf|Else|else|endif)(?:\((?<Content>(?:[^()]+|(?<Open>\()|(?<-Open>\)))+)\))?\]", RegexOptions.IgnoreCase);
        //public static readonly Regex IfElseIfTimeSpan = new Regex(@"@\[(?<Tag>if|elseIf|Else|else|endif)(?:\((?<Content>(?:[^()]+|(?<Open>\()|(?<-Open>\)))+)\))?\]", RegexOptions.IgnoreCase | RegexOptions.Compiled, TimeSpan.FromSeconds(10));
        //public static readonly Regex IfInPattern = new Regex(@"('[a-zA-Z0-9]+')\s+IN\s+((?:'[a-zA-Z0-9]+'(?:\s*,\s*'[a-zA-Z0-9]+')*))", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        //public static readonly Regex GeneralOperand = new Regex("(\\s*[^\\s|&=><!]+)+");
        //public static readonly Regex ParenthOperand = new Regex("\\([^\\(\\)]*(((?<Open>\\()[^\\(\\)]*)+((?<Close-Open>\\))[^\\(\\)]*)+)*(?(Open)(?!))\\)\\s*");
        //public static readonly Regex ConditionalOperand = new Regex("([|&=><!]{1,2}\\s*)");
        //public static readonly Regex ConditionalIn = new Regex(@"(\s*'[^']*'\s*)\s*IN\s*(\s*'[^']*'\s*(?:,\s*'[^']*'\s*)*)", RegexOptions.IgnoreCase | RegexOptions.Multiline);
        //public static readonly string EvaluateExpressionQuotes = @"(?<!')\b(?:[a-zA-Z']+(?:\s+[a-zA-Z']+)*)\b(?!')|(?<!')\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:[+-]\d{2}:\d{2})?(?!')";

        //public static readonly Regex Unicode = new Regex("[^\\u0000-\\u007F]", RegexOptions.Compiled);
        //public static readonly Regex NumberCheck = new Regex("^[-+]?[0-9]+[.]?[0-9]*([eE][-+]?[0-9]+)?$", RegexOptions.Compiled);
        //public static readonly Regex ReplaceRegex = new Regex("({\\s*\\\\stylesheet((\\s*[^{}\\\\]*{[^{}]*(((?<Open>{)[^{}]*)+((?<Close-Open>})[^{}]*)+)*(?(Open)(?!))})+[^{}]*)+}\\s*)  # Remove StyleSheet\r\n\t\t\t\t|(\\s*{\\\\\\*\\\\themedata [a-fA-F0-9\\s]*}\\s*)  # Remove Theme Data\r\n\t\t\t\t|(\\s*{\\\\\\*\\\\datastore [a-fA-F0-9\\s]*}\\s*)  #  Remove DataStore\r\n\t\t\t\t|(\\s*{\\\\\\*\\\\colorschememapping [a-fA-F0-9\\s]*}\\s*)  # Remove ColorSchemeMapping\r\n\t\t\t\t|({\\s*\\\\\\*\\\\latentstyles([^{}]*(\\s*[^{}\\\\]*{[^{}]*(((?<Open>{)[^{}]*)+((?<Close-Open>})[^{}]*)+)*(?(Open)(?!))})+[^{}]*)+}\\s*)  # Remove Style and Formatting Restrictions\r\n\t\t\t\t|({\\\\info\\s*({[^{}]*(((?<Open>{)[^{}]*)+((?<Close-Open>})[^{}]*)+)*(?(Open)(?!))}\\s*)+}\\s*)  # Remove Subdocument Info Group\r\n\t\t\t\t|(\\\\adeflang[0-9]*)  # Remove Extra adeflang tag\r\n\t\t\t\t|(\\\\rtf[0-9]+)  # Remove Redundant rtfN tag\r\n\t\t\t\t|({\\\\\\*\\\\defchp\\s*[(\\\\a-zA-Z0-9 )]*\\s*})  # Remove unnecessary default character formatting information\r\n\t\t\t\t|({\\\\\\*\\\\defpap\\s*[(\\\\a-zA-Z0-9 )]*\\s*})  # Remove unnecessary default paragraph formatting information\r\n\t\t\t\t", RegexOptions.Compiled | RegexOptions.IgnorePatternWhitespace);
        //public static readonly Regex SingleTestRegEx = new Regex("[^&|!=<>\\(\\)]+\\s*[=><!]{1,2}(\\s*[^&|!=<>\\(\\)\\s]+)+", RegexOptions.Compiled);
        //public static readonly Regex ParenthRegEx = new Regex("[^\\(|&]((\\([^\\(\\)]*(((?<Open>\\()[^\\(\\)]*)+((?<Close-Open>\\))[^\\(\\)]*)+)*(?(Open)(?!))\\)\\s*)\\s*&&\\s*(\\([^\\(\\)]*(((?<Open>\\()[^\\(\\)]*)+((?<Close-Open>\\))[^\\(\\)]*)+)*(?(Open)(?!))\\)\\s*))+[^\\)|&]", RegexOptions.Compiled);
        //public static readonly Regex ExtraRegEx = new Regex("\\(\\s*\\(\\s*[^\\(\\)]*\\s*\\)\\s*\\)", RegexOptions.Compiled);

        //public static readonly Regex Verbatim = new Regex("@[^]@[]*\\[Verbatim\\s*(\\([^\\(\\)]*(((?<Open>\\()[^\\(\\)]*)+((?<Close-Open>\\))[^\\(\\)]*)+)*(?(Open)(?!))\\)\\s*)\\s*[^]@[]*\\]");
    }
}
