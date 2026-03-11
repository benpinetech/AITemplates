using DocumentGenerator.Exceptions;
using DocumentGenerator.Models;
using DocumentGenerator.Utils;
using NCalc;
using PineCone.BuildingBlocks.Data.Constants;
using PineCone.BuildingBlocks.Data.Resources;
using PineCone.BuildingBlocks.Data.Results;
using PineCone.BuildingBlocks.SharedUtilities;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace DocumentGenerator;

public class DocumentGeneratorService
{
    private readonly DataProviderService _dataProviderService;

    public DocumentGeneratorService(DataProviderService dataProviderService)
    {
        _dataProviderService = dataProviderService;
    }

    private int MaxFillPoints = 75;
    private int MaxIfBlockFillPoints = 30;
    private int MaxForEachFillPoints = 30;
    private int MaxNestedLoops = 3;

    private const string VariableStartString = "@[";
    Dictionary<string, string> subDocDict = new(StringComparer.OrdinalIgnoreCase);
    private static class MethodName
    {
        public const string GET_BY_ID = "GetById";
        // public const string GET_BY_QUERY = "GetByQuery";
    }
    private static class FieldName
    {
        public const string ROOT_ID = "RootID";
    }

    private async Task UpdateMaxControls()
    {
        //int maxFillPoints, int maxIfBlockFillPoints, int maxForEachFillPoints, int maxNestedLoops
        Dictionary<string, string> Filters = new Dictionary<string, string> { { "Category", "Template" }, { "IsDeleted", "false" }, { "PageSize", "10" } };
        var command = new GetDataCommand { Entity = "SystemSetting", Method = "GetByQuery", RootID = null, Filters = Filters };
        var systemSettings = await _dataProviderService.GetDocumentVariableValue(command, true);

        if (!string.IsNullOrEmpty(systemSettings))
        {
            var systemSettingsResult = JsonSerializer.Deserialize<List<SystemSettingResource>>(systemSettings);
            if (systemSettingsResult != null && systemSettingsResult.Count > 0)
            {
                foreach (var setting in systemSettingsResult)
                {
                    switch (setting.Key.ToLower())
                    {
                        case "maxfillpoints":
                            int maxfillpoitns;
                            if (Int32.TryParse(setting.Value, out maxfillpoitns))
                            {
                                MaxFillPoints = maxfillpoitns;
                            }
                            break;
                        case "maxifblockfillpoints":
                            int maxifblockfillpoints;
                            if (Int32.TryParse(setting.Value, out maxifblockfillpoints))
                            {
                                MaxIfBlockFillPoints = maxifblockfillpoints;
                            }
                            break;
                        case "maxforeachfillpoints":
                            int maxforeachfillpoints;
                            if (Int32.TryParse(setting.Value, out maxforeachfillpoints))
                            {
                                MaxForEachFillPoints = maxforeachfillpoints;
                            }
                            break;
                        case "maxnestedloops":
                            int maxnestedloops;
                            if (Int32.TryParse(setting.Value, out maxnestedloops))
                            {
                                MaxNestedLoops = maxnestedloops;
                            }
                            break;
                    }
                }
            }
        }
    }

    public async Task<ProcessedTemplate> GenerateCaseDocument(string decodedTemplateText,
        Dictionary<string, string> templateParameters, bool useResource = true)
    {
        var logger = new TemplateLogger(displayLogsToConsole: false);
        await UpdateMaxControls();
        var processedTemplate = new ProcessedTemplate()
        {
            DecodedTemplateText = new StringBuilder(decodedTemplateText),
            Variables = ConvertDictToCaseInsensitiveDict(templateParameters),
            UseResource = useResource
        };
        logger.AddLog("GenerateCaseDocument", $"Initial variables: {string.Join(", ", processedTemplate.Variables.Keys)}", DocumentTemplateUtil.TemplateLogType.INFO);
        // Extract images before processing variables
        var images = ExtractImages(processedTemplate.DecodedTemplateText, logger);
        int loopCount = 0;
        while (true)
        {
            // 1. Work on the CURRENT StringBuilder content
            string currentText = processedTemplate.DecodedTemplateText.ToString();

            // 2. Find the LEFTMOST fill-point in the CURRENT string
            Match match = RtfRegularExpressions.RtfFillPointRegex.Match(currentText);
            if (!match.Success) break;

            // 3. Create FillPoint using the match from the CURRENT string
            var fillPoint = new FillPoint(match, logger);

            // 4. Use the match.Index and match.Length that are valid RIGHT NOW
            int currentIndex = match.Index;
            int oldLength = match.Length;

            // 5. Process it
            processedTemplate = await HandleVariable(
                processedTemplate,
                fillPoint,
                logger,
                currentIndex,
                oldLength
            );

            loopCount++;
            if (loopCount >= MaxFillPoints)
            {
                logger.AddLog("GenerateCaseDocument", $"Fill point count exceeded maximum of {MaxFillPoints}", DocumentTemplateUtil.TemplateLogType.ERROR);
                break;
            }
        }
        // Cleanup leftover end tags
        var cleanedText = processedTemplate.DecodedTemplateText.ToString();
        var cleanupMatches = RtfRegularExpressions.RtfFillPointRegex.Matches(cleanedText).Cast<Match>().OrderByDescending(m => m.Index).ToList();
        foreach (var match in cleanupMatches)
        {
            var fillPoint = new FillPoint(match, logger);
            var lower = fillPoint.CleanedVariable.ToLower();
            if (lower.EndsWith("endif]") || lower.EndsWith("else]") || lower.EndsWith("elseif]") ||
                lower.EndsWith("endforeach]") || lower.EndsWith("endcca]") || lower.EndsWith("endlb]"))
            {
                processedTemplate.DecodedTemplateText.Remove(match.Index, match.Length).Insert(match.Index, "");
                logger.AddLog("GenerateCaseDocument", $"Removed leftover tag: {fillPoint.FullVariable}", DocumentTemplateUtil.TemplateLogType.INFO);
            }
        }
        RestoreImages(processedTemplate.DecodedTemplateText, images, logger);
        processedTemplate.LogRows = logger.LogRows;
        return processedTemplate;
    }

    // handler methods begin
    private async Task<ProcessedTemplate> HandleVariable(ProcessedTemplate processedTemplate,
        FillPoint fillPoint,
        TemplateLogger logger,
        int currentIndex = -1,
        int variableLength = 0)
    {
        var variableRootName = GetVariableRootName(fillPoint.CleanedVariable, logger);
        var lowerRoot = variableRootName.ToLowerInvariant();

        if (!VariableIsValid(variableRootName, processedTemplate.Variables, logger))
        {
            logger.AddLog("HandleVariable", $"Variable '{fillPoint.CleanedVariable}' is not defined", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        logger.AddLog("HandleVariable", $"Handling variable with root name: {variableRootName} at index {currentIndex}", DocumentTemplateUtil.TemplateLogType.INFO);


        // Function switch
        processedTemplate = variableRootName.ToLower() switch
        {
            VariableType.CREATE_VAR_FUNCTION => await HandleCreateVar(fillPoint, processedTemplate, logger, currentIndex, variableLength),
            VariableType.IF_FUNCTION => await HandleIfFunction(fillPoint, processedTemplate, logger, currentIndex, variableLength),
            VariableType.SUBDOC => await HandleSubDocFunction(fillPoint, processedTemplate, logger, currentIndex, variableLength),
            VariableType.GET_AGE => await HandleGetAge(fillPoint, processedTemplate, logger, currentIndex, variableLength),
            VariableType.GET_DATE_DIFF => await HandleGetDateDiff(fillPoint, processedTemplate, logger, currentIndex, variableLength),
            VariableType.CCA => await HandleCca(fillPoint, processedTemplate, logger, currentIndex, variableLength),
            VariableType.LB => await HandleLb(fillPoint, processedTemplate, logger, currentIndex, variableLength),
            _ => processedTemplate
        };
        if (!variableRootName.Equals(VariableType.CCA, StringComparison.OrdinalIgnoreCase) &&
            !variableRootName.Equals(VariableType.LB, StringComparison.OrdinalIgnoreCase))
        {
            if (fillPoint.CleanedVariable.Contains(ArrayFunction.FOR_EACH, StringComparison.InvariantCultureIgnoreCase))
            {
                processedTemplate = await ProcessForEach(fillPoint, processedTemplate, logger, currentIndex, variableLength);
            }
            else if (fillPoint.CleanedVariable.Contains(ArrayFunction.CCA, StringComparison.InvariantCultureIgnoreCase))
            {
                processedTemplate = await ProcessCca(fillPoint, processedTemplate, logger, currentIndex, variableLength);
            }
            else if (fillPoint.CleanedVariable.Contains(ArrayFunction.LB, StringComparison.InvariantCultureIgnoreCase))
            {
                processedTemplate = await ProcessLb(fillPoint, processedTemplate, logger, currentIndex, variableLength);
            }
            else if (processedTemplate.Variables.ContainsKey(variableRootName))
            {
                processedTemplate = await HandleFillVariable(fillPoint, processedTemplate, logger, currentIndex, variableLength);
            }
        }
        return processedTemplate;
    }

    private async Task<ProcessedTemplate> HandleSubDocFunction(FillPoint fillPoint, ProcessedTemplate processedTemplate,
        TemplateLogger logger,
        int currentIndex,
        int variableLength)
    {
        // var example : @[Subdocument(1)]
        var argstr = ExtractContentInParentheses(fillPoint.CleanedVariable, logger);
        argstr = argstr.Trim();
        if (string.IsNullOrEmpty(argstr))
        {
            logger.AddLog("HandleSubDocFunction", $"Unable to parse Template Code for {fillPoint.CleanedVariable}", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        else
        {
            string decodedTemplateText;

            DocumentTemplateWithCollectionsResult? subTemplate = null;

            if (processedTemplate.UseResource)
            {
                subTemplate = await _dataProviderService.GetTemplateResource(argstr);
                if (subTemplate.Prompts.Count > 0)
                {
                    logger.AddLog("HandleSubDocFunction", $"Subdocument contains 1 or more prompts. Prompts are not allowed for use in subDocuments.", DocumentTemplateUtil.TemplateLogType.ERROR);
                }
                decodedTemplateText = DocumentTemplateUtil.DecodeTemplateTextForUsage(subTemplate.TemplateText);
            }
            else
            {
                subTemplate = await _dataProviderService.GetTemplateResource(argstr);
                decodedTemplateText = DocumentTemplateUtil.DecodeTemplateTextForUsage(subTemplate.TemplateText);
            }
            // Get built in variables
            subDocDict.Clear();
            //Dictionary<string, string> inputVars = new(StringComparer.OrdinalIgnoreCase);
            if (!processedTemplate.Variables.TryGetValue("builtin", out var builtInData))
            {
                logger.AddLog("HandleSubDocFunction", $"Unable to retrieve builtins", DocumentTemplateUtil.TemplateLogType.ERROR);
                processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
                return processedTemplate;
            }
            subDocDict.Add("builtin", builtInData);
            //handle document variables
            if (subTemplate.Variables.Count > 0)
            {
                var processedVariables = await TemplateProcessingUtil.ProcessTemplateVariables(subTemplate.Variables, subDocDict, _dataProviderService, processedTemplate.UseResource);
                if (processedVariables.Count > 0)
                    subDocDict.AddOrUpdate(processedVariables);
            }

            var processedSubdocument = await GenerateCaseDocument(decodedTemplateText, subDocDict, processedTemplate.UseResource);
            var subDocText = processedSubdocument.DecodedTemplateText.ToString();
            // Adjust font/color numbers
            var fontTableMatch = RtfRegularExpressions.FontTable.Match(subDocText);
            var colorTableMatch = RtfRegularExpressions.ColorTable.Match(subDocText);
            int maxFontNumber = fontTableMatch.Success ? ParseFontTable(fontTableMatch.Value) : 0;
            int maxColorNumber = colorTableMatch.Success ? ParseColorTable(colorTableMatch.Value) : 0;
            subDocText = ConvertDocumentFontsColors(subDocText, maxFontNumber, maxColorNumber);
            processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, subDocText);
            return processedTemplate;
        }
    }

    private async Task<ProcessedTemplate> HandleFillVariable(FillPoint fillPoint, ProcessedTemplate processedTemplate,
        TemplateLogger logger,
        int currentIndex,
        int variableLength)
    {
        string value = await GetFillPointValue(fillPoint.CleanedVariable, processedTemplate, logger);
        if (string.IsNullOrWhiteSpace(value))
        {
            value = "";
        }
        if (!string.IsNullOrWhiteSpace(fillPoint.RtfPrefix))
        {
            value = EscapeRtfValue(value, logger);
            value = $"{{{fillPoint.RtfPrefix}{value}{GetRtfReset(fillPoint.RtfPrefix, logger)}}}";
        }
        processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, value);
        return processedTemplate;
    }

    private async Task<ProcessedTemplate> HandleCreateVar(FillPoint fillPoint, ProcessedTemplate processedTemplate,
       TemplateLogger logger,
       int currentIndex,
       int variableLength)
    {
        logger.AddLog("HandleCreateVar", $"Processing CreateVar: {fillPoint.CleanedVariable} at index {currentIndex}", DocumentTemplateUtil.TemplateLogType.INFO);
        var argumentsStr = ExtractContentInParentheses(fillPoint.CleanedVariable, logger);
        argumentsStr = RemoveReturnsFromString(argumentsStr);
        var arguments = BreakArgumentStringIntoArgumentList(argumentsStr, logger);
        if (arguments.Count != 2)
        {
            logger.AddLog("HandleCreateVar", $"Invalid arguments for {fillPoint.CleanedVariable}, expected 2, got {arguments.Count}", DocumentTemplateUtil.TemplateLogType.ERROR);
            return processedTemplate;
        }
        var varName = RemoveAtSignFromBeginningOfString(arguments[0], logger);
        if (processedTemplate.Variables.ContainsKey(varName))
        {
            logger.AddLog("HandleCreateVar", $"Variable {varName} already exists, removing fillpoint.", DocumentTemplateUtil.TemplateLogType.WARNING);
            processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        var createVarString = arguments[1];
        var firstFunctionDotIndex = GetIndexOfDotAfterFinalParenthesis(createVarString);
        var additionalArgs = new List<string>();
        if (firstFunctionDotIndex >= 0)
        {
            var additionalArgsStr = createVarString.Substring(firstFunctionDotIndex + 1);
            createVarString = createVarString.Substring(0, firstFunctionDotIndex);
            additionalArgs.AddRange(additionalArgsStr.Split('.'));
        }

        string varData;
        try
        {
            varData = await GetVariableData(createVarString, processedTemplate, logger);
            if (string.IsNullOrWhiteSpace(varData) || varData == "{}")
            {
                logger.AddLog("HandleCreateVar", $"Data retrieval for {createVarString} returned empty or invalid, setting to empty array", DocumentTemplateUtil.TemplateLogType.WARNING);
                varData = "[]";
            }
        }
        catch (Exception e)
        {
            logger.AddLog("HandleCreateVar", $"Error retrieving data for {createVarString}: {e.Message}, setting to empty array", DocumentTemplateUtil.TemplateLogType.ERROR);
            varData = "[]";
        }
        processedTemplate.Variables[varName] = varData;
        logger.AddLog("HandleCreateVar", $"Set variable {varName} to: {varData}", DocumentTemplateUtil.TemplateLogType.INFO);
        string replacement = fillPoint.RtfPrefix.Length > 0 ? $"{{{fillPoint.RtfPrefix}{GetRtfReset(fillPoint.RtfPrefix, logger)}}}" : "";
        processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, replacement);
        if (additionalArgs.Any())
        {
            var adjustedFillPoint = $"@[{varName}";
            foreach (var arg in additionalArgs)
            {
                adjustedFillPoint += $".{arg}";
            }
            adjustedFillPoint += "]";
            try
            {
                var newVal = await GetFillPointValue(adjustedFillPoint, processedTemplate, logger);
                processedTemplate.Variables[varName] = newVal;
                logger.AddLog("HandleCreateVar", $"Applied additional args to {varName}, new value: {newVal}", DocumentTemplateUtil.TemplateLogType.INFO);
            }
            catch (Exception e)
            {
                logger.AddLog("HandleCreateVar", $"Error applying additional args to {varName}: {e.Message}, keeping original value", DocumentTemplateUtil.TemplateLogType.ERROR);
            }
        }
        logger.AddLog("HandleCreateVar", $"Variable {varName} added successfully", DocumentTemplateUtil.TemplateLogType.INFO);
        return processedTemplate;
    }

    private async Task<ProcessedTemplate> HandleIfFunction(FillPoint fillPoint, ProcessedTemplate processedTemplate,
        TemplateLogger logger,
        int currentIndex,
        int variableLength)
    {
        logger.AddLog("HandleIfFunction", $"Handling If Statement starting with: {fillPoint.CleanedVariable} at index {currentIndex}", DocumentTemplateUtil.TemplateLogType.INFO);

        // Extract the if block using the proven safe method
        string fullIfBlock = ExtractCompleteIfBlock(fillPoint.FullVariable, processedTemplate, logger);
        if (string.IsNullOrEmpty(fullIfBlock))
        {
            processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }

        //Find where this block starts in the current StringBuilder
        string currentText = processedTemplate.DecodedTemplateText.ToString();
        int actualBlockStart = currentText.IndexOf(fullIfBlock, StringComparison.Ordinal);
        if (actualBlockStart == -1)
        {
            logger.AddLog("HandleIfFunction", "If block extracted but not found in current text — possible double-processing", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }

        //Extract and evaluate conditionals
        var conditionalStatements = ExtractConditionalStatementsFromIfBlock(fullIfBlock, logger);

        int trueIndex = -1;
        for (int k = 0; k < conditionalStatements.Count; k++)
        {
            var (tag, _, _) = conditionalStatements[k];
            string lower = tag.ToLowerInvariant();
            if (lower.StartsWith("@[if(") || lower.StartsWith("@[elseif("))
            {
                logger.AddLog("EvaluateConditionalStatement", $"Pre-evaluating statement: {tag}", DocumentTemplateUtil.TemplateLogType.INFO);
                if (await EvaluateConditionalStatement(tag, processedTemplate, logger))
                {
                    trueIndex = k;
                    break;
                }
            }
        }

        string relevantContent = "";
        if (trueIndex != -1)
        {
            var (startTag, startIdx, startLen) = conditionalStatements[trueIndex];
            string nextCond;
            int nextIdx;
            if (trueIndex + 1 < conditionalStatements.Count)
            {
                var nextTuple = conditionalStatements[trueIndex + 1];
                nextCond = nextTuple.tag;
                nextIdx = nextTuple.index;
            }
            else
            {
                nextCond = "@[endif]";
                var endifTuple = conditionalStatements.FirstOrDefault(t => t.tag.ToLowerInvariant() == nextCond);
                nextIdx = endifTuple.index;
            }
            relevantContent = GetStringBetweenConditionals(fullIfBlock, startTag, nextCond, logger);
            relevantContent = Regex.Replace(relevantContent, @"\\par\s*$", "", RegexOptions.IgnoreCase).Trim();
            logger.AddLog("HandleIfFunction", $"ReleveantContent return as: {relevantContent}", DocumentTemplateUtil.TemplateLogType.INFO);
        }
        else
        {
            int elseIndex = conditionalStatements.FindIndex(t => t.tag.ToLowerInvariant() == "@[else]");
            if (elseIndex != -1)
            {
                var (elseTag, elseStart, elseLength) = conditionalStatements[elseIndex];
                string endTag = "@[endif]";
                var endifIndex = conditionalStatements.FindIndex(i => i.index > conditionalStatements[elseIndex].index && i.tag.ToLowerInvariant() == endTag);
                if (endifIndex != -1)
                {
                    var (endifTag, endifStart, _) = conditionalStatements[endifIndex];
                    relevantContent = fullIfBlock.Substring(elseStart + elseLength, endifStart - (elseStart + elseLength)).Trim();
                }
            }
        }

        string replacement = string.IsNullOrWhiteSpace(fillPoint.RtfPrefix)
         ? relevantContent
         : $"{{{fillPoint.RtfPrefix}{EscapeRtfValue(relevantContent, logger)}{GetRtfReset(fillPoint.RtfPrefix, logger)}}}".Trim();
        processedTemplate.DecodedTemplateText.Remove(actualBlockStart, fullIfBlock.Length);
        processedTemplate.DecodedTemplateText.Insert(actualBlockStart, replacement);

        logger.AddLog("HandleIfFunction", $"If block fully replaced. Removed {fullIfBlock.Length} chars, inserted {replacement.Length}", DocumentTemplateUtil.TemplateLogType.INFO);

        return processedTemplate;
    }

    private async Task<ProcessedTemplate> HandleGetAge(FillPoint fillPoint, ProcessedTemplate processedTemplate,
        TemplateLogger logger,
        int currentIndex,
        int variableLength)
    {
        logger.AddLog("HandleGetAge", $"Processing GetAge: {fillPoint.CleanedVariable}", DocumentTemplateUtil.TemplateLogType.INFO);
        var argstr = ExtractContentInParentheses(fillPoint.CleanedVariable, logger);
        var arguments = BreakArgumentStringIntoArgumentList(argstr, logger);
        if (arguments.Count != 2)
        {
            logger.AddLog("HandleGetAge", $"Invalid arguments for {fillPoint.CleanedVariable}, expected 2, got {arguments.Count}", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        // Resolve nested fillpoints
        var birthDateStr = await GetFillPointValue(arguments[0], processedTemplate, logger);
        var refDateStr = await GetFillPointValue(arguments[1], processedTemplate, logger);
        logger.AddLog("HandleGetAge", $"Resolved arguments: birthDate={birthDateStr}, refDate={refDateStr}", DocumentTemplateUtil.TemplateLogType.INFO);
        var age = DocumentTemplateUtil.StandaloneFunctions.GetAge(birthDateStr, refDateStr);
        logger.AddLog("HandleGetAge", $"Computed age: {age}", DocumentTemplateUtil.TemplateLogType.INFO);
        processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, age);
        return processedTemplate;
    }

    private async Task<ProcessedTemplate> HandleGetDateDiff(FillPoint fillPoint, ProcessedTemplate processedTemplate,
        TemplateLogger logger,
        int currentIndex,
        int variableLength)
    {
        logger.AddLog("HandleGetDateDiff", $"Processing GetDateDiff: {fillPoint.CleanedVariable}", DocumentTemplateUtil.TemplateLogType.INFO);
        var argstr = ExtractContentInParentheses(fillPoint.CleanedVariable, logger);
        var arguments = BreakArgumentStringIntoArgumentList(argstr, logger);
        if (arguments.Count != 3)
        {
            logger.AddLog("HandleGetDateDiff", $"Invalid arguments for {fillPoint.CleanedVariable}, expected 3, got {arguments.Count}", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        // Resolve nested fillpoints
        var date1Str = await GetFillPointValue(arguments[0], processedTemplate, logger);
        var date2Str = await GetFillPointValue(arguments[1], processedTemplate, logger);
        var unit = arguments[2].Trim().Trim('"');
        logger.AddLog("HandleGetDateDiff", $"Resolved arguments: date1={date1Str}, date2={date2Str}, unit={unit}", DocumentTemplateUtil.TemplateLogType.INFO);
        var diff = DocumentTemplateUtil.StandaloneFunctions.GetDateDiff(date1Str, date2Str, unit);
        logger.AddLog("HandleGetDateDiff", $"Computed date diff: {diff}", DocumentTemplateUtil.TemplateLogType.INFO);
        processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, diff);
        return processedTemplate;
    }

    private async Task<ProcessedTemplate> HandleCca(FillPoint fillPoint, ProcessedTemplate processedTemplate,
        TemplateLogger logger,
        int currentIndex,
        int variableLength)
    {
        logger.AddLog("HandleCca", $"Processing Cca (shortcut): {fillPoint.CleanedVariable}", DocumentTemplateUtil.TemplateLogType.INFO);
        var argstr = ExtractContentInParentheses(fillPoint.CleanedVariable, logger);
        var arguments = BreakArgumentStringIntoArgumentList(argstr, logger);
        if (arguments.Count != 1)
        {
            logger.AddLog("HandleCca", $"Invalid arguments for {fillPoint.CleanedVariable}, expected 1 for shortcut, got {arguments.Count}", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        var tableChain = arguments[0].Trim();  // e.g., "Witness.FormatName(F M L)"
        var split = tableChain.Split('.');
        if (split.Length < 2)
        {
            logger.AddLog("HandleCca", $"Invalid table.chain format: {tableChain} (needs at least one dot)", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        var table = split[0].Trim();
        var chain = string.Join(".", split.Skip(1)).Trim();  // e.g., "FormatName(F M L)" or "FirstName.SetCasing(upper)"
        var varData = GetVariableValueFromVariableList(table, processedTemplate.Variables, logger);
        if (string.IsNullOrWhiteSpace(varData))
        {
            processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        if (!GetVariableDataIsArray(varData, logger))
        {
            logger.AddLog("HandleCca", $"Variable {table} is not an array", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        var jsonArray = JsonDocument.Parse(varData).RootElement;
        List<string> items = new();
        string? originalTemp = null;
        bool hadTemp = processedTemplate.Variables.TryGetValue("_tempRow", out originalTemp);  // Use "_tempRow" to avoid conflicts
        foreach (var element in jsonArray.EnumerateArray())
        {
            var rowData = element.GetRawText();
            processedTemplate.Variables["_tempRow"] = rowData;
            var fieldValue = await GetFillPointValue($"@[_tempRow.{chain}]", processedTemplate, logger);
            if (!string.IsNullOrWhiteSpace(fieldValue))
            {
                items.Add(fieldValue);
            }
        }
        if (hadTemp)
        {
            processedTemplate.Variables["_tempRow"] = originalTemp!;
        }
        else
        {
            processedTemplate.Variables.Remove("_tempRow");
        }
        var output = JoinWithCommasAndAnd(items);
        processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, output);
        return processedTemplate;
    }

    private async Task<ProcessedTemplate> HandleLb(FillPoint fillPoint, ProcessedTemplate processedTemplate,
        TemplateLogger logger,
        int currentIndex,
        int variableLength)
    {
        logger.AddLog("HandleLb", $"Processing Lb (shortcut): {fillPoint.CleanedVariable}", DocumentTemplateUtil.TemplateLogType.INFO);
        var argstr = ExtractContentInParentheses(fillPoint.CleanedVariable, logger);
        var arguments = BreakArgumentStringIntoArgumentList(argstr, logger);
        if (arguments.Count != 1)
        {
            logger.AddLog("HandleLb", $"Invalid arguments for {fillPoint.CleanedVariable}, expected 1 for shortcut, got {arguments.Count}", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        var tableChain = arguments[0].Trim();  // e.g., "Witness.FormatName(F M L)"
        var split = tableChain.Split('.');
        if (split.Length < 2)
        {
            logger.AddLog("HandleLb", $"Invalid table.chain format: {tableChain} (needs at least one dot)", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        var table = split[0].Trim();
        var chain = string.Join(".", split.Skip(1)).Trim();  // e.g., "FormatName(F M L)" or "FirstName.SetCasing(upper)"
        var varData = GetVariableValueFromVariableList(table, processedTemplate.Variables, logger);
        if (string.IsNullOrWhiteSpace(varData))
        {
            processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        if (!GetVariableDataIsArray(varData, logger))
        {
            logger.AddLog("HandleLb", $"Variable {table} is not an array", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        var jsonArray = JsonDocument.Parse(varData).RootElement;
        List<string> items = new();
        string? originalTemp = null;
        bool hadTemp = processedTemplate.Variables.TryGetValue("_tempRow", out originalTemp);
        foreach (var element in jsonArray.EnumerateArray())
        {
            var rowData = element.GetRawText();
            processedTemplate.Variables["_tempRow"] = rowData;
            var fieldValue = await GetFillPointValue($"@[_tempRow.{chain}]", processedTemplate, logger);
            if (!string.IsNullOrWhiteSpace(fieldValue))
            {
                items.Add(fieldValue);
            }
        }
        if (hadTemp)
        {
            processedTemplate.Variables["_tempRow"] = originalTemp!;
        }
        else
        {
            processedTemplate.Variables.Remove("_tempRow");
        }
        // TO DO: Update to look for FormatType in builtins for plain text and html formats
        string? output = null;
        string formatType = null;

        if (processedTemplate.Variables.TryGetValue("builtin", out var builtinJson) && !string.IsNullOrEmpty(builtinJson))
        {
            try
            {
                var builtinDict = JsonSerializer.Deserialize<Dictionary<string, string>>(builtinJson);
                formatType = builtinDict?.GetValueOrDefault("FormattingType", "rtf")?.ToLower();  // Default to "rtf" if missing
                logger.AddLog("ProcessLb", $"Extracted formatType: '{formatType}' from builtin", DocumentTemplateUtil.TemplateLogType.INFO);
            }
            catch (Exception ex)
            {
                logger.AddLog("ProcessLb", $"Failed to parse builtin JSON: {ex.Message}. Defaulting to 'rtf'.", DocumentTemplateUtil.TemplateLogType.WARNING);
                formatType = "rtf";
            }
        }
        else
        {
            logger.AddLog("ProcessLb", "No 'builtin' found in variables. Defaulting to 'rtf'.", DocumentTemplateUtil.TemplateLogType.WARNING);
            formatType = "rtf";
        }

        if (string.IsNullOrEmpty(formatType))
        {
            formatType = "rtf";  // Final fallback
        }

        switch (formatType.ToLower())
        {
            case DocumentTemplateFormattingType.Html:
                output = string.Join("<br/>", items);
                break;
            case DocumentTemplateFormattingType.Plaintext:
                output = string.Join(Environment.NewLine, items);
                break;
            case DocumentTemplateFormattingType.Rtf:
                output = string.Join(" \\par ", items);
                break;
            default:
                output = string.Join(Environment.NewLine, items);
                break;
        }
        processedTemplate.DecodedTemplateText = processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, output);
        return processedTemplate;
    }
    // handler methods end

    // helper methods begin
    private async Task<StringBuilder> FillVariableInTextString(string variable, StringBuilder textString, ProcessedTemplate processedTemplate, TemplateLogger logger, bool isCondition = false, string rtfPrefix = "")
    {
        logger.AddLog("FillVariableInTextString", $"Filling variable {variable} with RTF prefix: {rtfPrefix}", DocumentTemplateUtil.TemplateLogType.INFO);
        string value;
        try
        {
            value = await GetFillPointValue(variable, processedTemplate, logger);
            if (string.IsNullOrWhiteSpace(value))
            {
                value = isCondition ? "null" : "";  // "null" only for conditions
                logger.AddLog("FillVariableInTextString", $"Value was empty; set to '{value}' for {(isCondition ? "condition" : "regular")} use.", DocumentTemplateUtil.TemplateLogType.ERROR);
            }
        }
        catch (Exception e)
        {
            logger.AddLog("FillVariableInTextString", $"Error filling {variable}: {e.Message}. Using 'null' for safety.", DocumentTemplateUtil.TemplateLogType.ERROR);
            value = "null";  // Catch-all for errors
        }

        if (isCondition && string.IsNullOrWhiteSpace(value))
        {
            value = "null";  // Double-check for conditions
        }

        if (!string.IsNullOrWhiteSpace(rtfPrefix))
        {
            value = EscapeRtfValue(value, logger);
            value = $"{{{rtfPrefix}{value}{GetRtfReset(rtfPrefix, logger)}}}";
        }

        textString = ReplaceVariableInTextString(variable, value, textString, logger);
        return textString;
    }

    private string EscapeRtfValue(string value, TemplateLogger logger)
    {
        if (string.IsNullOrEmpty(value)) return value;
        value = value.Replace("\\", "\\\\").Replace("{", "\\{").Replace("}", "\\}");
        value = RtfRegularExpressions.Unicode.Replace(value, m => $"\\u{(int)m.Value[0]}?");
        value = value.Replace(Environment.NewLine, " \\par ");
        return value;
    }

    private string GetRtfReset(string prefix, TemplateLogger logger)
    {
        var resets = new List<string>();
        if (prefix.Contains("\\b")) resets.Add("\\b0");
        if (prefix.Contains("\\i")) resets.Add("\\i0");
        if (prefix.Contains("\\ul")) resets.Add("\\ulnone");
        // Add more for other toggle controls (e.g., \strike)
        return string.Join("", resets);
    }

    private async Task<string> GetFillPointValue(string fillPoint, ProcessedTemplate processedTemplate, TemplateLogger logger)
    {
        // expected values input examples:
        // @[builtin]
        // @[builtin.CaseID]
        // @[builtin.today.formatDate(yyyy-mm-dd hh:mm)] (or other functions)
        // @[myCase.type.getLabel(78).SetCasing(upper)] (or more functions)
        fillPoint = RemoveBrackets(fillPoint, logger);
        var splitArgs = SplitArgumentsInFillPoint(fillPoint, logger);
        if (splitArgs.Any() == false)
            return "";  // Early return for invalid fillpoints

        try
        {
            var varData = GetVariableValueFromVariableList(splitArgs[0], processedTemplate.Variables, logger);
            if (splitArgs.Count == 1)
                return string.IsNullOrWhiteSpace(varData) ? "" : varData;

            return GetVariableDataIsArray(varData, logger) ?
                (await ProcessFillPointArray(splitArgs, varData, processedTemplate, logger)) :
                (await ProcessFillPointObject(splitArgs, varData, processedTemplate, logger));
        }
        catch (Exception ex)
        {
            logger.AddLog("GetFillPointValue", $"Error resolving fillpoint '{fillPoint}': {ex.Message}. Returning ''.", DocumentTemplateUtil.TemplateLogType.ERROR);
            return "null";  // Safe fallback
        }
    }

    private async Task<string> ProcessFillPointArray(List<string> splitArgs, string varData,
        ProcessedTemplate processedTemplate, TemplateLogger logger)
    {
        // splitArgs[0] = Variable name, I.E. cip, caseAssignment, myCase, etc.
        // splitArgs[1] = Array Functions, I.E. ForEach(), First(), Last()
        // splitArgs[2] = field name if First() or Last()
        // splitArgs[3+] = Function, I.E. SetCasing, GetLabel, MrMrs, etc. if First() or Last()
        var function = splitArgs[1];
        if (function.Contains('('))
        {
            function = function[..function.IndexOf("(", StringComparison.Ordinal)];
        }
        if (string.Equals(function, ArrayFunction.ANY, StringComparison.OrdinalIgnoreCase))
        {
            return JsonArrayHasElements.Run(varData);
        }
        if (string.Equals(function, ArrayFunction.NUM_ITEMS, StringComparison.OrdinalIgnoreCase))
        {
            try
            {
                return JsonDocument.Parse(varData).RootElement.GetArrayLength().ToString();
            }
            catch (JsonException ex)
            {
                logger.AddLog("ProcessFillPointArray", $"Error getting number of items for {splitArgs[0]}: {ex.Message}. Returning '0'.", DocumentTemplateUtil.TemplateLogType.ERROR);
                return "0";
            }
        }
        return (await ProcessFirstOrLastArrayFunction(function, varData, splitArgs, processedTemplate, logger));
    }

    private async Task<ProcessedTemplate> ProcessForEach(FillPoint fillPoint, ProcessedTemplate processedTemplate,
        TemplateLogger logger,
        int currentIndex = -1,
        int variableLength = 0)
    {
        /*
         * EXAMPLE:
         * @[CreateVar(@assignments, @CaseAssignment.GetByQuery("CaseID":@[builtin.CaseID], "type": "ATTY"))]
         * @[assignments.ForEach(assignment)]
         * @[CreateVar(@addrs, @NameAddress.GetByQuery("NameID":@[assignment.NameID]))]
         * @[addrs.foreach(addr)]
         * @addr
         * @[addrs.EndForEach]
         * @[assignments.EndForEach]
         *
         * INPUTS:
         * VARIABLE: The full name of the variable, I.E. @[assignments.ForEach(assignment)]
         */
        logger.AddLog("ProcessForEach", $"Processing ForEach with variable: {fillPoint.CleanedVariable}, RTF prefix: {fillPoint.RtfPrefix}, at index: {currentIndex}", DocumentTemplateUtil.TemplateLogType.INFO);
        var variable = RemoveBrackets(fillPoint.CleanedVariable, logger);
        var splitArgs = SplitArgumentsInFillPoint(variable, logger);
        if (splitArgs.Any() == false)
        {
            logger.AddLog("ProcessForEach", $"Invalid arguments for {variable}", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        var varData = GetVariableValueFromVariableList(splitArgs[0], processedTemplate.Variables, logger);
        if (!IsJsonArrayWithObjects(varData))
        {
            logger.AddLog("ProcessForEach", $"Variable Data is not an array, cannot process {variable}", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        var varName = splitArgs[0];
        var startTag = fillPoint.FullVariable;  // Includes RTF prefix
        var text = processedTemplate.DecodedTemplateText.ToString();
        var blockStartIndex = text.IndexOf(startTag, currentIndex, StringComparison.Ordinal);
        if (blockStartIndex != currentIndex)
        {
            logger.AddLog("ProcessForEach", $"ForEach block at {currentIndex} not found or misaligned", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        var endRegex = new Regex($@"(?:\\[^{{[\]]}}+)*@\[\s*{Regex.Escape(varName)}\s*\.\s*{ArrayFunction.END_FOR_EACH}\s*\]", RegexOptions.IgnoreCase);
        var endMatch = endRegex.Match(text, blockStartIndex + startTag.Length);
        if (!endMatch.Success)
        {
            logger.AddLog("ProcessForEach", $"Cannot find end tag for {varName}", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        var endIndex = endMatch.Index;
        var fullEndTag = endMatch.Value;
        var blockLength = endIndex - blockStartIndex + fullEndTag.Length;
        var forEachBlock = text.Substring(blockStartIndex, blockLength);
        var innerContentStartIndex = startTag.Length;
        var innerContentLength = endIndex - (blockStartIndex + startTag.Length);
        if (innerContentLength < 0)
        {
            logger.AddLog("ProcessForEach", "Invalid inner content length - skipping processing", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText.Remove(currentIndex, variableLength).Insert(currentIndex, "");
            return processedTemplate;
        }
        var contentToRepeat = forEachBlock.Substring(innerContentStartIndex, innerContentLength).Trim();
        var jsonArray = JsonDocument.Parse(varData).RootElement;
        var loopVarName = ExtractContentInParentheses(splitArgs[1], logger);
        string? originalLoopVarValue = null;
        bool loopVarExisted = processedTemplate.Variables.TryGetValue(loopVarName, out originalLoopVarValue);
        var result = new StringBuilder();
        int loopCount = 0;
        foreach (var element in jsonArray.EnumerateArray())
        {
            var loopVarValue = element.GetRawText();
            using var elemDoc = JsonDocument.Parse(loopVarValue);
            if (elemDoc.RootElement.ValueKind == JsonValueKind.Object)
            {
                var properties = elemDoc.RootElement.EnumerateObject();
                if (properties.Count() == 1)
                {
                    var prop = properties.First();
                    if (string.Equals(prop.Name, varName, StringComparison.OrdinalIgnoreCase))
                    {
                        loopVarValue = prop.Value.GetRawText();
                    }
                }
            }
            processedTemplate.Variables[loopVarName] = loopVarValue;
            var loopTemplate = new ProcessedTemplate()
            {
                Variables = processedTemplate.Variables,
                DecodedTemplateText = new StringBuilder(contentToRepeat),
                NestedLoopCount = processedTemplate.NestedLoopCount + 1,
                UseResource = processedTemplate.UseResource
            };
            if (loopTemplate.NestedLoopCount > MaxNestedLoops)
            {
                logger.AddLog("ProcessForEach", "Nested loop count exceeded max number of loops", DocumentTemplateUtil.TemplateLogType.ERROR);
                break;
            }

            int innerLoopCount = 0;
            while (true)
            {
                var loopText = loopTemplate.DecodedTemplateText.ToString();
                var match = RtfRegularExpressions.RtfFillPointRegex.Match(loopText);  // Leftmost match
                if (!match.Success) break;

                var loopFillPoint = new FillPoint(match, logger);
                loopTemplate = await HandleVariable(loopTemplate, loopFillPoint, logger, match.Index, match.Length);

                innerLoopCount++;
                if (innerLoopCount >= MaxForEachFillPoints)
                {
                    logger.AddLog("ProcessForEach", $"Fill point count inside ForEach exceeded maximum of {MaxForEachFillPoints}", DocumentTemplateUtil.TemplateLogType.ERROR);
                    break;
                }
            }

            var item = loopTemplate.DecodedTemplateText.ToString().Trim();
            if (!string.IsNullOrWhiteSpace(item))
            {
                result.Append(item);
                if (loopCount < jsonArray.GetArrayLength() - 1)
                {
                    string formatType;
                    processedTemplate.Variables.TryGetValue("FormattingType", out formatType);
                    if (!string.IsNullOrEmpty(formatType))
                    {
                        switch (formatType.ToLower())
                        {
                            case DocumentTemplateFormattingType.Html:
                                result.Append("<br/>");
                                break;
                            case DocumentTemplateFormattingType.Plaintext:
                                result.Append(Environment.NewLine);
                                break;
                            case DocumentTemplateFormattingType.Rtf:
                                result.Append(" \\par ");
                                break;
                            default:
                                result.Append(Environment.NewLine);
                                break;
                        }
                    }
                }
            }
            loopCount++;
        }
        if (loopVarExisted)
        {
            processedTemplate.Variables[loopVarName] = originalLoopVarValue!;
        }
        else
        {
            processedTemplate.Variables.Remove(loopVarName);
        }
        string output = result.ToString();
        if (!string.IsNullOrWhiteSpace(output) && !string.IsNullOrWhiteSpace(fillPoint.RtfPrefix))
        {
            output = EscapeRtfValue(output, logger);
            output = $"{{{fillPoint.RtfPrefix}{output}{GetRtfReset(fillPoint.RtfPrefix, logger)}}}";
            logger.AddLog("ProcessForEach", $"Applied RTF: '{output}'", DocumentTemplateUtil.TemplateLogType.INFO);
        }
        processedTemplate.DecodedTemplateText.Remove(currentIndex, blockLength).Insert(currentIndex, output);
        return processedTemplate;
    }

    private async Task<ProcessedTemplate> ProcessLb(FillPoint fillPoint, ProcessedTemplate processedTemplate,
        TemplateLogger logger,
        int currentIndex = -1,
        int variableLength = 0)
    {
        logger.AddLog("ProcessLb", $"Processing Lb with variable: {fillPoint.CleanedVariable}, RTF prefix: {fillPoint.RtfPrefix}, at index: {currentIndex}", DocumentTemplateUtil.TemplateLogType.INFO);
        var variable = RemoveBrackets(fillPoint.CleanedVariable, logger);
        var splitArgs = SplitArgumentsInFillPoint(variable, logger);
        if (splitArgs.Count < 2)
        {
            logger.AddLog("ProcessLb", $"Invalid arguments for {variable}, expected at least 2", DocumentTemplateUtil.TemplateLogType.ERROR);
            return processedTemplate;
        }
        var startTag = fillPoint.FullVariable;
        var varName = splitArgs[0];
        var function = splitArgs[1];
        var loopVarName = ExtractContentInParentheses(function, logger);
        var varData = GetVariableValueFromVariableList(varName, processedTemplate.Variables, logger);
        if (!IsJsonArrayWithObjects(varData))
        {
            logger.AddLog("ProcessLb", $"Variable Data is not an array, cannot process {variable}", DocumentTemplateUtil.TemplateLogType.ERROR);
            return processedTemplate;
        }
        var startIndex = processedTemplate.DecodedTemplateText.ToString().IndexOf(fillPoint.FullVariable, currentIndex, StringComparison.OrdinalIgnoreCase);
        if (startIndex != currentIndex)
        {
            logger.AddLog("ProcessLb", $"Lb block at {currentIndex} not found or misaligned", DocumentTemplateUtil.TemplateLogType.ERROR);
            return processedTemplate;
        }
        var text = processedTemplate.DecodedTemplateText.ToString();
        var potentialEndMatches = RtfRegularExpressions.RtfFillPointRegex.Matches(text, startIndex + startTag.Length)
            .Cast<Match>()
            .ToList();

        Match? endMatch = null;
        foreach (var candidate in potentialEndMatches)
        {
            var candidateFillPoint = new FillPoint(candidate, logger);
            var cleanedCandidate = Normalize(candidateFillPoint.CleanedVariable);
            var expectedEnd = Normalize($"@[{varName}.{ArrayFunction.END_LB}]");
            logger.AddLog("ProcessLb", $"Checking candidate: {cleanedCandidate} vs expected: {expectedEnd}", DocumentTemplateUtil.TemplateLogType.INFO);
            if (string.Equals(cleanedCandidate, expectedEnd, StringComparison.OrdinalIgnoreCase))
            {
                endMatch = candidate;
                break;
            }
        }
        if (endMatch == null)
        {
            logger.AddLog("ProcessLb", $"Cannot find end tag for {varName}. Removing start tag to prevent loop.", DocumentTemplateUtil.TemplateLogType.ERROR);
            // Fallback: Remove start tag to break infinite loop
            processedTemplate.DecodedTemplateText.Remove(startIndex, startTag.Length).Insert(startIndex, "");
            return processedTemplate;
        }
        var endIndex = endMatch.Index;
        var fullEndTag = endMatch.Value;
        var blockLength = endIndex - startIndex + fullEndTag.Length;
        if (blockLength <= startTag.Length + fullEndTag.Length)
        {
            logger.AddLog("ProcessLb", $"Invalid block length {blockLength} - skipping processing", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText.Remove(startIndex, blockLength).Insert(startIndex, "");
            return processedTemplate;
        }
        var lbBlock = text.Substring(startIndex, blockLength);
        var innerContentStartIndex = startTag.Length;
        var innerContentLength = endIndex - (startIndex + startTag.Length);
        var contentToRepeat = lbBlock.Substring(innerContentStartIndex, innerContentLength).Trim();
        logger.AddLog("ProcessLb", $"Extracted content to repeat: '{contentToRepeat}' (length: {contentToRepeat.Length})", DocumentTemplateUtil.TemplateLogType.INFO);
        var jsonArray = JsonDocument.Parse(varData).RootElement;
        string? originalLoopVarValue = null;
        bool loopVarExisted = processedTemplate.Variables.TryGetValue(loopVarName, out originalLoopVarValue);
        List<string> items = new();
        int loopCount = 0;
        foreach (var element in jsonArray.EnumerateArray())
        {
            var loopVarValue = element.GetRawText();
            using var elemDoc = JsonDocument.Parse(loopVarValue);
            if (elemDoc.RootElement.ValueKind == JsonValueKind.Object)
            {
                var properties = elemDoc.RootElement.EnumerateObject();
                if (properties.Count() == 1)
                {
                    var prop = properties.First();
                    if (string.Equals(prop.Name, varName, StringComparison.OrdinalIgnoreCase))
                    {
                        loopVarValue = prop.Value.GetRawText();
                    }
                }
            }
            processedTemplate.Variables[loopVarName] = loopVarValue;
            var loopTemplate = new ProcessedTemplate()
            {
                Variables = processedTemplate.Variables,
                DecodedTemplateText = new StringBuilder(contentToRepeat),
                NestedLoopCount = processedTemplate.NestedLoopCount + 1,
                UseResource = processedTemplate.UseResource
            };
            if (loopTemplate.NestedLoopCount > MaxNestedLoops)
            {
                logger.AddLog("ProcessLb", "Nested loop count exceeded max number of loops", DocumentTemplateUtil.TemplateLogType.ERROR);
                break;
            }

            int innerLoopCount = 0;
            while (true)
            {
                var loopText = loopTemplate.DecodedTemplateText.ToString();
                var match = RtfRegularExpressions.RtfFillPointRegex.Match(loopText);  // Leftmost match
                if (!match.Success) break;

                var loopFillPoint = new FillPoint(match, logger);
                loopTemplate = await HandleVariable(loopTemplate, loopFillPoint, logger, match.Index, match.Length);

                innerLoopCount++;
                if (innerLoopCount >= MaxForEachFillPoints)
                {
                    logger.AddLog("ProcessLb", $"Fill point count inside Lb exceeded maximum of {MaxForEachFillPoints}", DocumentTemplateUtil.TemplateLogType.ERROR);
                    break;
                }
            }

            var rawItem = loopTemplate.DecodedTemplateText.ToString();
            logger.AddLog("ProcessLb", $"Raw item after processing (loop {loopCount + 1}): '{rawItem}' (length: {rawItem.Length})", DocumentTemplateUtil.TemplateLogType.INFO);

            var item = rawItem;
            if (!string.IsNullOrWhiteSpace(item))
            {
                items.Add(item);
                logger.AddLog("ProcessLb", $"Added item (loop {loopCount + 1}): '{item}'", DocumentTemplateUtil.TemplateLogType.INFO);
            }
            else
            {
                logger.AddLog("ProcessLb", $"Skipped empty item (loop {loopCount + 1})", DocumentTemplateUtil.TemplateLogType.WARNING);
            }
            loopCount++;
        }
        if (loopVarExisted)
        {
            processedTemplate.Variables[loopVarName] = originalLoopVarValue!;
        }
        else
        {
            processedTemplate.Variables.Remove(loopVarName);
        }
        string? output = null;
        string formatType = null;

        if (processedTemplate.Variables.TryGetValue("builtin", out var builtinJson) && !string.IsNullOrEmpty(builtinJson))
        {
            try
            {
                var builtinDict = JsonSerializer.Deserialize<Dictionary<string, string>>(builtinJson);
                formatType = builtinDict?.GetValueOrDefault("FormattingType", "rtf")?.ToLower();  // Default to "rtf" if missing
                logger.AddLog("ProcessLb", $"Extracted formatType: '{formatType}' from builtin", DocumentTemplateUtil.TemplateLogType.INFO);
            }
            catch (Exception ex)
            {
                logger.AddLog("ProcessLb", $"Failed to parse builtin JSON: {ex.Message}. Defaulting to 'rtf'.", DocumentTemplateUtil.TemplateLogType.WARNING);
                formatType = "rtf";
            }
        }
        else
        {
            logger.AddLog("ProcessLb", "No 'builtin' found in variables. Defaulting to 'rtf'.", DocumentTemplateUtil.TemplateLogType.WARNING);
            formatType = "rtf";
        }

        if (string.IsNullOrEmpty(formatType))
        {
            formatType = "rtf";  // Final fallback
        }

        switch (formatType.ToLower())
        {
            case DocumentTemplateFormattingType.Html:
                output = string.Join("<br/>", items);
                break;
            case DocumentTemplateFormattingType.Plaintext:
                output = string.Join(Environment.NewLine, items);
                break;
            case DocumentTemplateFormattingType.Rtf:
                output = string.Join(" \\par ", items);
                break;
            default:
                output = string.Join(Environment.NewLine, items);
                break;
        }
        logger.AddLog("ProcessLb", $"Final output: '{output}' (items count: {items.Count})", DocumentTemplateUtil.TemplateLogType.INFO);
        if (!string.IsNullOrWhiteSpace(output) && !string.IsNullOrWhiteSpace(fillPoint.RtfPrefix))
        {
            output = EscapeRtfValue(output, logger);
            output = $"{{{fillPoint.RtfPrefix}{output}{GetRtfReset(fillPoint.RtfPrefix, logger)}}}";
            logger.AddLog("ProcessLb", $"Applied RTF: '{output}'", DocumentTemplateUtil.TemplateLogType.INFO);
        }
        processedTemplate.DecodedTemplateText.Remove(currentIndex, blockLength).Insert(currentIndex, output ?? "");
        return processedTemplate;
    }

    private async Task<ProcessedTemplate> ProcessCca(FillPoint fillPoint, ProcessedTemplate processedTemplate,
        TemplateLogger logger,
        int currentIndex = -1,
        int variableLength = 0)
    {
        logger.AddLog("ProcessCca", $"Processing Cca with variable: {fillPoint.CleanedVariable}, RTF prefix: {fillPoint.RtfPrefix}, at index: {currentIndex}", DocumentTemplateUtil.TemplateLogType.INFO);
        var variable = RemoveBrackets(fillPoint.CleanedVariable, logger);
        var splitArgs = SplitArgumentsInFillPoint(variable, logger);
        if (splitArgs.Count < 2)
        {
            logger.AddLog("ProcessCca", $"Invalid arguments for {variable}, expected at least 2", DocumentTemplateUtil.TemplateLogType.ERROR);
            return processedTemplate;
        }
        var startTag = fillPoint.FullVariable;
        var varName = splitArgs[0];
        var function = splitArgs[1];
        var loopVarName = ExtractContentInParentheses(function, logger);
        var varData = GetVariableValueFromVariableList(varName, processedTemplate.Variables, logger);
        if (!IsJsonArrayWithObjects(varData))
        {
            logger.AddLog("ProcessCca", $"Variable Data is not an array, cannot process {variable}", DocumentTemplateUtil.TemplateLogType.ERROR);
            return processedTemplate;
        }
        var startIndex = processedTemplate.DecodedTemplateText.ToString().IndexOf(fillPoint.FullVariable, currentIndex, StringComparison.OrdinalIgnoreCase);
        if (startIndex != currentIndex)
        {
            logger.AddLog("ProcessCca", $"Cca block at {currentIndex} not found or misaligned", DocumentTemplateUtil.TemplateLogType.ERROR);
            return processedTemplate;
        }

        var text = processedTemplate.DecodedTemplateText.ToString();
        var potentialEndMatches = RtfRegularExpressions.RtfFillPointRegex.Matches(text, startIndex + startTag.Length)
            .Cast<Match>()
            .ToList();

        Match? endMatch = null;
        foreach (var candidate in potentialEndMatches)
        {
            var candidateFillPoint = new FillPoint(candidate, logger);
            var cleanedCandidate = Normalize(candidateFillPoint.CleanedVariable);
            var expectedEnd = Normalize($"@[{varName}.{ArrayFunction.END_CCA}]");
            logger.AddLog("ProcessCca", $"Checking candidate: {cleanedCandidate} vs expected: {expectedEnd}", DocumentTemplateUtil.TemplateLogType.INFO);
            if (string.Equals(cleanedCandidate, expectedEnd, StringComparison.OrdinalIgnoreCase))
            {
                endMatch = candidate;
                break;
            }
        }
        if (endMatch == null)
        {
            logger.AddLog("ProcessCca", $"Cannot find end tag for {varName}", DocumentTemplateUtil.TemplateLogType.ERROR);
            // Fallback: Remove start tag to break infinite loop
            processedTemplate.DecodedTemplateText.Remove(startIndex, startTag.Length).Insert(startIndex, "");
            return processedTemplate;
        }
        var endIndex = endMatch.Index;
        var fullEndTag = endMatch.Value;
        var blockLength = endIndex - startIndex + fullEndTag.Length;
        if (blockLength <= startTag.Length + fullEndTag.Length)
        {
            logger.AddLog("ProcessCca", $"Invalid block length {blockLength} - skipping processing", DocumentTemplateUtil.TemplateLogType.ERROR);
            processedTemplate.DecodedTemplateText.Remove(startIndex, blockLength).Insert(startIndex, "");
            return processedTemplate;
        }

        var ccaBlock = text.Substring(startIndex, blockLength);
        var innerContentStartIndex = startTag.Length;
        var innerContentLength = endIndex - (startIndex + startTag.Length);

        var contentToRepeat = ccaBlock.Substring(innerContentStartIndex, innerContentLength).Trim();
        var jsonArray = JsonDocument.Parse(varData).RootElement;
        string? originalLoopVarValue = null;
        bool loopVarExisted = processedTemplate.Variables.TryGetValue(loopVarName, out originalLoopVarValue);
        List<string> items = new();
        int loopCount = 0;
        foreach (var element in jsonArray.EnumerateArray())
        {
            var loopVarValue = element.GetRawText();
            using var elemDoc = JsonDocument.Parse(loopVarValue);
            if (elemDoc.RootElement.ValueKind == JsonValueKind.Object)
            {
                var properties = elemDoc.RootElement.EnumerateObject();
                if (properties.Count() == 1)
                {
                    var prop = properties.First();
                    if (string.Equals(prop.Name, varName, StringComparison.OrdinalIgnoreCase))
                    {
                        loopVarValue = prop.Value.GetRawText();
                    }
                }
            }
            processedTemplate.Variables[loopVarName] = loopVarValue;
            var loopTemplate = new ProcessedTemplate()
            {
                Variables = processedTemplate.Variables,
                DecodedTemplateText = new StringBuilder(contentToRepeat),
                NestedLoopCount = processedTemplate.NestedLoopCount + 1,
                UseResource = processedTemplate.UseResource
            };
            if (loopTemplate.NestedLoopCount > MaxNestedLoops)
            {
                logger.AddLog("ProcessCca", "Nested loop count exceeded max number of loops", DocumentTemplateUtil.TemplateLogType.ERROR);
                break;
            }

            int innerLoopCount = 0;
            while (true)
            {
                var loopText = loopTemplate.DecodedTemplateText.ToString();
                var match = RtfRegularExpressions.RtfFillPointRegex.Match(loopText);  // Leftmost match
                if (!match.Success) break;

                var loopFillPoint = new FillPoint(match, logger);
                loopTemplate = await HandleVariable(loopTemplate, loopFillPoint, logger, match.Index, match.Length);

                innerLoopCount++;
                if (innerLoopCount >= MaxForEachFillPoints)
                {
                    logger.AddLog("ProcessCca", $"Fill point count inside Cca exceeded maximum of {MaxForEachFillPoints}", DocumentTemplateUtil.TemplateLogType.ERROR);
                    break;
                }
            }

            var item = loopTemplate.DecodedTemplateText.ToString().Trim();
            if (!string.IsNullOrWhiteSpace(item))
            {
                items.Add(item);
            }
            loopCount++;
        }
        if (loopVarExisted)
        {
            processedTemplate.Variables[loopVarName] = originalLoopVarValue!;
        }
        else
        {
            processedTemplate.Variables.Remove(loopVarName);
        }
        var output = JoinWithCommasAndAnd(items);
        if (!string.IsNullOrWhiteSpace(output) && !string.IsNullOrWhiteSpace(fillPoint.RtfPrefix))
        {
            output = EscapeRtfValue(output, logger);
            output = $"{{{fillPoint.RtfPrefix}{output}{GetRtfReset(fillPoint.RtfPrefix, logger)}}}";
            logger.AddLog("ProcessCca", $"Applied RTF: '{output}'", DocumentTemplateUtil.TemplateLogType.INFO);
        }
        processedTemplate.DecodedTemplateText.Remove(currentIndex, blockLength).Insert(currentIndex, output);
        return processedTemplate;
    }

    private async Task<string> ProcessFirstOrLastArrayFunction(string function, string varData, List<string> splitArgs,
        ProcessedTemplate processedTemplate, TemplateLogger logger)
    {
        var rowData = string.Empty;
        if (string.Equals(function, ArrayFunction.FIRST, StringComparison.OrdinalIgnoreCase))
        {
            rowData = GetFirstOrLastElement(varData, getFirst: true, logger);
        }
        else if (string.Equals(function, ArrayFunction.LAST, StringComparison.OrdinalIgnoreCase))
        {
            rowData = GetFirstOrLastElement(varData, getFirst: false, logger);
        }
        if (splitArgs.Count == 2)
            return rowData;
        splitArgs.RemoveRange(0, 2);
        return (await GetFieldValueAndRunFunctions(splitArgs, rowData, processedTemplate, logger));
    }

    private async Task<string> ProcessFillPointObject(List<string> splitArgs, string varData,
        ProcessedTemplate processedTemplate, TemplateLogger logger)
    {
        // splitArgs[0] = Variable name, I.E. cip, caseAssignment, myCase, etc.
        // splitArgs[1] = Field Name, I.E. PersonnelFirstName, Type, etc.
        // splitArgs[2+] = Function, I.E. SetCasing, GetLabel, MrMrs etc.
        // remove variable name from list
        splitArgs.RemoveAt(0);
        return (await GetFieldValueAndRunFunctions(splitArgs, varData, processedTemplate, logger));
    }

    private async Task<string> GetFieldValueAndRunFunctions(List<string> remainingArguments, string varData,
        ProcessedTemplate processedTemplate, TemplateLogger logger)
    {
        // expected inputs
        // remainingArguments: list of remaining arguments. first value could be field name, or object function
        // remaining data should just be functions
        // varData: the JSON data string containing the field data
        // processedTemplate: the template that is currently processing
        var firstArg = remainingArguments[0];
        var fieldValue = varData;
        // Check if firstArg is a field name (no parentheses)
        if (!firstArg.Contains('('))
        {
            fieldValue = GetFieldValueFromVariable(firstArg, varData, logger);
            remainingArguments.RemoveAt(0);
        }
        // If firstArg has parentheses, treat it as a function and don't modify fieldValue yet
        foreach (var function in remainingArguments)
        {
            var parenIndex = function.IndexOf('(');
            var funcName = function[..parenIndex].ToLower();
            var args = ExtractContentInParentheses(function, logger);
            switch (funcName)
            {
                case DocumentTemplateUtil.ChainedFunctions.FORMAT_DATE_FUNC:
                    fieldValue = DateFormat.Run(fieldValue, args);
                    break;
                case DocumentTemplateUtil.ObjectFunctions.FORMAT_NAME:
                    fieldValue = FormatName.Run(fieldValue, args);
                    break;
                case DocumentTemplateUtil.ChainedFunctions.SET_STRING_CASING:
                    fieldValue = SetCasing.Run(fieldValue, args);
                    break;
                case DocumentTemplateUtil.ChainedFunctions.GET_STRING_SEGMENT:
                    string[] segments = args.Split(',')
                       .Select(item => item.Trim())
                       .ToArray();
                    if (segments.Length < 2)
                    {
                        logger.AddLog("GetFieldValueAndRunFunctions", $"Unable to get string segment.  Set string requires 2 arguments, first/last and length.  arguments provided were: {args}", DocumentTemplateUtil.TemplateLogType.ERROR);
                    }
                    if (int.TryParse(segments[1], out var length) == false)
                    {
                        logger.AddLog("GetFieldValueAndRunFunctions",
                            $"Unable to get string segment. second argument requires an integer, but '{length}' was provided.", DocumentTemplateUtil.TemplateLogType.ERROR);
                        break;
                    }
                    fieldValue = GetStringSegment.Run(fieldValue, segments[0], length);
                    break;
                case DocumentTemplateUtil.ChainedFunctions.GET_LABEL:
                    if (int.TryParse(args.Trim(), out var dropdownId) == false)
                    {
                        logger.AddLog("GetFieldValueAndRunFunctions",
                            "Unable to convert string to Integer value for GetLabel Function:" +
                            $"Field Name: {firstArg}, String Input: {args.Trim()}", DocumentTemplateUtil.TemplateLogType.ERROR);
                        break;
                    }
                    fieldValue = await _dataProviderService.GetLabel(dropdownId, fieldValue, processedTemplate.UseResource);
                    break;
                case DocumentTemplateUtil.ChainedFunctions.IS_NULL_OR_EMPTY:
                    fieldValue = IsNullOrEmpty.Run(fieldValue);
                    break;
                case DocumentTemplateUtil.ChainedFunctions.FORMAT_NUMBER:
                    fieldValue = FormatNumber.Run(fieldValue, args);
                    break;
                case DocumentTemplateUtil.ChainedFunctions.FORMAT_PHONE_NUMBER:
                    fieldValue = FormatPhoneNumber.Run(fieldValue);
                    break;
                default:
                    logger.AddLog("GetFieldValueAndRunFunctions",
                        $"Unknown function: {funcName}", DocumentTemplateUtil.TemplateLogType.ERROR);
                    break;
            }
        }
        return fieldValue;
    }

    private async Task<string> FillConditionWithVariableValues(string initialCondition, ProcessedTemplate processedTemplate, TemplateLogger logger)
    {
        // example input: @[myCase.type] == FEL || @[myCase.type] == MISD
        // example output FEL == FEL || FEL == MISD
        var condition = new StringBuilder(initialCondition);
        // extract variables
        var curVarIndex = GetNextVarIndex(condition, logger);
        var loopCount = 0;
        while (curVarIndex != -1)
        {
            var variable = GetFullVariableString(condition, curVarIndex, logger);
            condition = await FillVariableInTextString(variable, condition, processedTemplate, logger, true);
            curVarIndex = GetNextVarIndex(condition, logger);
            loopCount++;
            if (loopCount >= MaxIfBlockFillPoints)
            {
                logger.AddLog("FillConditionWithVariableValues",
                    $"Fillpoint Count exceeded maximum of {MaxIfBlockFillPoints} for If Statement", DocumentTemplateUtil.TemplateLogType.ERROR);
                break;
            }
        }
        return condition.ToString();
    }

    private async Task<bool> EvaluateConditionalStatement(string condition, ProcessedTemplate processedTemplate, TemplateLogger logger)
    {
        // expected input Examples:
        // @[if(@[myCase.ReceivedDate] == @[builtin.Today])]
        // @[if(@[myCase.type] == 'FEL' || @[myCase.type] == 'MISD')]
        // @[elseIf(@[myCase.status] == 'OPN' && @[myCase.Type] == 'JUV')]
        // @[Else]
        logger.AddLog("EvaluateConditionalStatement", $"Pre-evaluating statement: {condition}", DocumentTemplateUtil.TemplateLogType.INFO);
        if (string.Equals(condition, "@[else]", StringComparison.OrdinalIgnoreCase))
            return true;
        var conditionalStatementStr = ExtractContentInParentheses(condition, logger);
        try
        {
            if (conditionalStatementStr.Contains("@["))
            {
                conditionalStatementStr = await FillConditionWithVariableValues(conditionalStatementStr, processedTemplate, logger);
            }
        }
        catch (Exception e)
        {
            logger.AddLog("EvaluateConditionalStatement", $"error: {e.Message}", DocumentTemplateUtil.TemplateLogType.ERROR);
            return false;
        }
        logger.AddLog("EvaluateConditionalStatement", $"Evaluating statement: {conditionalStatementStr}", DocumentTemplateUtil.TemplateLogType.INFO);
        conditionalStatementStr = CleanConditionalForNCalc(conditionalStatementStr, logger);
        return EvaluateExpression(conditionalStatementStr, processedTemplate, logger);
    }

    private static string CleanConditionalForNCalc(string input, TemplateLogger logger)
    {
        var original = input;

        // Replace single = with == first (avoid double ==)
        input = Regex.Replace(input, @"(?<!=)\s*=\s*(?!=)", " == ");

        // Remove hyphens from literals
        input = Regex.Replace(input, @"'([^-']*)-([^-']*)'", "'$1$2'", RegexOptions.IgnoreCase);

        // Normalize spaces around operators
        string[] operators = { "IN", "==", "!=", ">", "<", ">=", "<=", "&&", "||" };
        foreach (var op in operators)
        {
            input = Regex.Replace(input, @"\s*" + Regex.Escape(op) + @"\s*", " " + op + " ", RegexOptions.IgnoreCase);
        }

        // Remove quotes from left operand of IN
        input = Regex.Replace(input, @"'([^']+)'(?= \s*IN \s*)", "$1", RegexOptions.IgnoreCase);

        // Quote unquoted literals (skip operators)
        input = Regex.Replace(input, @"(?<!')\b(?!IN\b|&&\b|\|\|\b)[a-zA-Z0-9]+\b(?!')", "'$0'", RegexOptions.IgnoreCase);

        // Unquote true/false
        input = Regex.Replace(input, @"'(true|True|TRUE)'", "true", RegexOptions.IgnoreCase);
        input = Regex.Replace(input, @"'(false|False|FALSE)'", "false", RegexOptions.IgnoreCase);

        logger.AddLog("CleanConditionalForNCalc", $"Original: {original} → Cleaned: \"{input}\"", DocumentTemplateUtil.TemplateLogType.INFO);

        return input;
    }

    private static string TransformExpressionForNCalc(string expression, ProcessedTemplate processedTemplate, TemplateLogger logger)
    {
        logger.AddLog("TransformExpressionForNCalc", $"Input: \"{expression}\"", DocumentTemplateUtil.TemplateLogType.INFO);

        // Transform IN to in('left', 'item1','item2',...)
        expression = RtfRegularExpressions.IfInPattern.Replace(expression, m =>
        {
            string left = m.Groups[1].Value.Trim();
            if (!left.StartsWith("'") && !left.EndsWith("'")) left = "'" + left + "'";
            string list = m.Groups[2].Value.Trim().Replace(" , ", ",").Replace(", ", ",");
            return "in(" + left + ", " + list + ")";
        });

        logger.AddLog("TransformExpressionForNCalc", $"Transformed: \"{expression}\"", DocumentTemplateUtil.TemplateLogType.INFO);

        return expression;
    }

    private static bool EvaluateExpression(string expression, ProcessedTemplate processedTemplate, TemplateLogger logger)
    {
        //Check if expression uses IN
        logger.AddLog("EvaluateExpression", $"Evaluating expression {expression}", DocumentTemplateUtil.TemplateLogType.INFO);

        expression = TransformExpressionForNCalc(expression, processedTemplate, logger);
        try
        {
            // Create a new NCalc Expression
            logger.AddLog("Debug", $"Loaded NCalc version: {typeof(NCalc.Expression).Assembly.GetName().Version}", DocumentTemplateUtil.TemplateLogType.INFO);
            var methods = typeof(NCalc.Expression).GetMethods().Where(m => m.Name == "Evaluate").Select(m => $"{m.Name}({string.Join(", ", m.GetParameters().Select(p => p.ParameterType.Name))})").ToArray();
            logger.AddLog("Debug", $"Available Evaluate overloads: {string.Join("; ", methods)}", DocumentTemplateUtil.TemplateLogType.INFO);
            Expression e = new Expression(expression);
            // Evaluate the expression
            var result = e.Evaluate();
            // Return the result as a boolean
            logger.AddLog("EvaluateExpression", $"Expression {expression} evaluated as {Convert.ToBoolean(result)}", DocumentTemplateUtil.TemplateLogType.INFO);
            return Convert.ToBoolean(result);
        }
        catch (Exception ex)
        {
            logger.AddLog("EvaluateExpression", $"Error during evaluation of expression {expression}, Error: {ex}", DocumentTemplateUtil.TemplateLogType.ERROR);
            return false;
        }
    }

    private static string GetStringBetweenConditionals(string input, string startTag, string endTag, TemplateLogger logger)
    {
        logger.AddLog("GetStringBetweenConditionals", $"Extracting content between {startTag} and {endTag}", DocumentTemplateUtil.TemplateLogType.INFO);
        int startIdx = -1;
        int endIdx = -1;
        int pos = 0;
        while (pos < input.Length)
        {
            var match = RtfRegularExpressions.RtfFillPointRegex.Match(input, pos);
            if (!match.Success) break;

            string cleaned = DocumentTemplateUtil.RemoveRtfMarkup(match.Value).Trim();

            if (startIdx == -1 && Normalize(cleaned).Equals(Normalize(startTag), StringComparison.OrdinalIgnoreCase))
            {
                startIdx = match.Index + match.Length;
            }
            else if (startIdx != -1 && Normalize(cleaned).Equals(Normalize(endTag), StringComparison.OrdinalIgnoreCase))
            {
                endIdx = match.Index;
                break;
            }

            pos = match.Index + match.Length;
        }

        if (startIdx == -1 || endIdx == -1 || endIdx <= startIdx)
            return string.Empty;

        return input.Substring(startIdx, endIdx - startIdx).Trim();
    }

    private static string JoinWithCommasAndAnd(List<string> items)
    {
        if (items.Count == 0) return string.Empty;
        if (items.Count == 1) return items[0];
        if (items.Count == 2) return $"{items[0]} and {items[1]}";
        var butLast = string.Join(", ", items.Take(items.Count - 1));
        return $"{butLast} and {items[^1]}";
    }

    private static string ExtractCompleteIfBlock(string variable, ProcessedTemplate processedTemplate, TemplateLogger logger)
    {
        logger.AddLog("ExtractCompleteIfBlock", $"Executing for variable: {variable}", DocumentTemplateUtil.TemplateLogType.INFO);
        var input = processedTemplate.DecodedTemplateText.ToString();
        var startIndex = input.IndexOf(variable, StringComparison.OrdinalIgnoreCase);
        if (startIndex == -1)
        {
            logger.AddLog("ExtractCompleteIfBlock", $"Variable '{variable}' not found", DocumentTemplateUtil.TemplateLogType.ERROR);
            return string.Empty;
        }
        // Get all fillpoints, sorted by index (ascending for stacking)
        var matches = RtfRegularExpressions.RtfFillPointRegex.Matches(input)
            .Cast<Match>()
            .Where(m => m.Index >= startIndex) // Only from this if onward
            .OrderBy(m => m.Index)
            .ToList();
        if (matches.Count == 0) return string.Empty;
        var ifStack = new Stack<Match>(); // Stack of 'if' matches
        string block = string.Empty;
        foreach (var match in matches)
        {
            string cleaned = new FillPoint(match, logger).CleanedVariable.ToLower(); // Use FillPoint for cleaning
            if (cleaned.StartsWith("@[if("))  // Changed: Push only for 'if', not 'elseif'
            {
                ifStack.Push(match);
            }
            else if (cleaned.StartsWith("@[endif]"))
            {
                if (ifStack.Count == 0)
                {
                    logger.AddLog("ExtractCompleteIfBlock", "Unmatched endif", DocumentTemplateUtil.TemplateLogType.ERROR);
                    return string.Empty;
                }
                var ifMatch = ifStack.Pop();
                if (ifStack.Count == 0)
                { // Outermost: this is our block
                    int end = match.Index + match.Length;
                    block = input.Substring(ifMatch.Index, end - ifMatch.Index);
                    break;
                }
            }
            // Handle else/elseif as part of the block (no push/pop)
        }
        if (ifStack.Count != 0 || string.IsNullOrEmpty(block))
        {
            logger.AddLog("ExtractCompleteIfBlock", "Unbalanced if/endif", DocumentTemplateUtil.TemplateLogType.ERROR);
            return string.Empty;
        }
        return block;
    }

    private static List<(string tag, int index, int length)> ExtractConditionalStatementsFromIfBlock(string ifBlock, TemplateLogger logger)
    {
        logger.AddLog("ExtractConditionalStatementsFromIfBlock", "Executing ExtractConditionalStatementsFromIfBlock", DocumentTemplateUtil.TemplateLogType.INFO);
        var conditionals = new List<(string tag, int index, int length)>();
        int i = 0;
        int n = ifBlock.Length;
        int nestingLevel = 0;
        while (i < n)
        {
            // Skip optional RTF control words before the tag
            while (i < n && ifBlock[i] == '\\')
            {
                i++; // Skip \
                while (i < n && !char.IsWhiteSpace(ifBlock[i]) && ifBlock[i] != '{' && ifBlock[i] != '}' && ifBlock[i] != '\\')
                {
                    i++; // Skip control word chars
                }
                // Skip optional space after control word
                if (i < n && char.IsWhiteSpace(ifBlock[i])) i++;
            }
            // Find next '@['
            int start = ifBlock.IndexOf("@[", i, StringComparison.Ordinal);
            if (start == -1) break;
            i = start;
            // Find the end: balanced ']'
            int j = start + 2; // After '@['
            int bracketCount = 1;
            while (j < n && bracketCount > 0)
            {
                if (ifBlock[j] == '[') bracketCount++;
                else if (ifBlock[j] == ']') bracketCount--;
                j++;
            }
            if (bracketCount != 0)
            {
                logger.AddLog("ExtractConditionalStatementsFromIfBlock", $"Unbalanced brackets at position {start}", DocumentTemplateUtil.TemplateLogType.ERROR);
                i = j;
                continue;
            }
            // Full raw tag including ']'
            string rawTag = ifBlock.Substring(start, j - start);
            // Skip RTF and whitespace after '[' to find tag name
            int nameStart = start + 2;
            while (nameStart < j)
            {
                if (char.IsWhiteSpace(ifBlock[nameStart]))
                {
                    nameStart++;
                    continue;
                }
                if (ifBlock[nameStart] == '\\')
                {
                    nameStart++; // Skip \
                    while (nameStart < j && !char.IsWhiteSpace(ifBlock[nameStart]) && ifBlock[nameStart] != '\\') nameStart++; // Skip control
                    if (nameStart < j && char.IsWhiteSpace(ifBlock[nameStart])) nameStart++; // Skip space
                    continue;
                }
                break;
            }
            // Extract tag name (letters only)
            int nameEnd = nameStart;
            while (nameEnd < j && char.IsLetter(ifBlock[nameEnd])) nameEnd++;
            string tagName = ifBlock.Substring(nameStart, nameEnd - nameStart).ToLowerInvariant();
            if (tagName == "elseif") tagName = "elseIf"; // Preserve standard casing
            string tagLower = tagName.ToLowerInvariant();
            // Skip RTF and whitespace after name to find content '(' if present
            int contentIndex = nameEnd;
            while (contentIndex < j)
            {
                if (char.IsWhiteSpace(ifBlock[contentIndex]))
                {
                    contentIndex++;
                    continue;
                }
                if (ifBlock[contentIndex] == '\\')
                {
                    contentIndex++; // Skip \
                    while (contentIndex < j && !char.IsWhiteSpace(ifBlock[contentIndex]) && ifBlock[contentIndex] != '\\') contentIndex++; // Skip control
                    if (contentIndex < j && char.IsWhiteSpace(ifBlock[contentIndex])) contentIndex++; // Skip space
                    continue;
                }
                break;
            }
            // Extract and clean content if '(' present
            string cleanedContent = string.Empty;
            if (contentIndex < j && ifBlock[contentIndex] == '(')
            {
                int parenStart = contentIndex;
                int parenCount = 1;
                contentIndex++; // Skip '('
                int rawContentStart = contentIndex;
                while (contentIndex < j && parenCount > 0)
                {
                    if (ifBlock[contentIndex] == '(') parenCount++;
                    else if (ifBlock[contentIndex] == ')') parenCount--;
                    contentIndex++;
                }
                if (parenCount == 0 && contentIndex <= j)
                {
                    string rawContent = ifBlock.Substring(rawContentStart, contentIndex - rawContentStart - 1); // Exclude ')'
                    cleanedContent = DocumentTemplateUtil.CleanConditionalContent(rawContent);
                }
                else
                {
                    logger.AddLog("ExtractConditionalStatementsFromIfBlock", $"Unbalanced parentheses in tag at {start}", DocumentTemplateUtil.TemplateLogType.ERROR);
                    i = j;
                    continue;
                }
            }
            // Rebuild clean tag without RTF
            string tag = "@[" + tagName +
                         (string.IsNullOrEmpty(cleanedContent) ? "" : "(" + cleanedContent + ")") +
                         "]";
            // Add only top-level tags, with index and length
            if (tagLower == "if")
            {
                if (nestingLevel == 0)
                {
                    conditionals.Add((tag, start, j - start));
                }
                nestingLevel++;
            }
            else if (tagLower == "endif")
            {
                if (nestingLevel == 1)
                {
                    conditionals.Add((tag, start, j - start));
                }
                if (nestingLevel > 0)
                {
                    nestingLevel--;
                }
            }
            else if (tagLower == "elseif" || tagLower == "else")
            {
                if (nestingLevel == 1)
                {
                    conditionals.Add((tag, start, j - start));
                }
            }
            i = j; // Move past ']'
        }
        if (nestingLevel != 0)
        {
            logger.AddLog("ExtractConditionalStatementsFromIfBlock", "Unbalanced if/endif in block", DocumentTemplateUtil.TemplateLogType.ERROR);
        }
        logger.AddLog("ExtractConditionalStatementsFromIfBlock", $"Extracted conditionals: {string.Join(", ", conditionals.Select(c => $"{c.tag} at {c.index}:{c.length}"))}", DocumentTemplateUtil.TemplateLogType.INFO);
        return conditionals;
    }

    private static bool VariableIsValid(string variableRootName, Dictionary<string, string> variables, TemplateLogger logger)
    {
        if (variables.ContainsKey(variableRootName)) return true; // Before other checks
        var rootNameIsValid = CheckVariableIsValid(variableRootName, variables, logger);
        if (rootNameIsValid == false)
            logger.AddLog("VariableIsValid", $"variable root name {variableRootName} is not valid", DocumentTemplateUtil.TemplateLogType.ERROR);
        return rootNameIsValid;
    }

    private static string GetVariableRootName(string variable, TemplateLogger logger)
    {
        if (!variable.StartsWith("@["))
        {
            logger.AddLog("GetVariableRootName", $"Variable '{variable}' does not start with '@['", DocumentTemplateUtil.TemplateLogType.ERROR);
            return string.Empty;
        }
        // Find the text after "@[" and before the first "." or "("
        const int startIndex = 2;
        var endIndex = variable.IndexOfAny(new char[] { '.', '(', ' ', ']' }, startIndex);
        if (endIndex == -1)
        {
            return string.Empty;
        }
        var rawRoot = variable.Substring(startIndex, endIndex - startIndex);
        var rootName = Regex.Replace(rawRoot, @"\s+", "").ToLowerInvariant();
        logger.AddLog("GetVariableRootName", $"Extracted root name: {rootName} from {variable}", DocumentTemplateUtil.TemplateLogType.INFO);
        return rootName;
    }

    private static bool CheckVariableIsValid(string variableRootName, Dictionary<string, string> variables, TemplateLogger logger)
    {
        logger.AddLog("CheckVariableIsValid", "Executing CheckVariableIsValid", DocumentTemplateUtil.TemplateLogType.INFO);
        if (VariableInVariableList(variableRootName, variables, logger))
            return true;
        else if (VariableIsFunction(variableRootName, logger))
            return true;
        return false;
    }

    private static bool VariableInVariableList(string variableRootName, Dictionary<string, string> variables, TemplateLogger logger)
    {
        logger.AddLog("VariableInVariableList", "Executing VariableInVariableList", DocumentTemplateUtil.TemplateLogType.INFO);
        return variables.ContainsKey(variableRootName);
    }

    private static bool VariableIsFunction(string variableRootName, TemplateLogger logger)
    {
        logger.AddLog("VariableIsFunction", "Executing VariableIsFunction", DocumentTemplateUtil.TemplateLogType.INFO);
        return string.Equals(variableRootName, VariableType.CREATE_VAR_FUNCTION, StringComparison.CurrentCultureIgnoreCase) ||
           string.Equals(variableRootName, VariableType.GET_AGE, StringComparison.CurrentCultureIgnoreCase) ||
           string.Equals(variableRootName, VariableType.GET_DATE_DIFF, StringComparison.CurrentCultureIgnoreCase) ||
           string.Equals(variableRootName, VariableType.IF_FUNCTION, StringComparison.CurrentCultureIgnoreCase) ||
           string.Equals(variableRootName, VariableType.SUBDOC, StringComparison.CurrentCultureIgnoreCase) ||
           string.Equals(variableRootName, VariableType.CCA, StringComparison.CurrentCultureIgnoreCase) ||
           string.Equals(variableRootName, VariableType.LB, StringComparison.CurrentCultureIgnoreCase);
    }

    private static StringBuilder ReplaceVariableInTextString(string variable, string replacement, StringBuilder textString, TemplateLogger logger)
    {
        logger.AddLog("ReplaceVariableInTextString", $"Replacing {variable} with {replacement}", DocumentTemplateUtil.TemplateLogType.INFO);
        int startIndex = textString.ToString().IndexOf(variable, StringComparison.OrdinalIgnoreCase);
        if (startIndex >= 0)
            textString.Replace(variable, replacement, startIndex, variable.Length);
        return textString;
    }

    private static string ExtractContentInParentheses(string input, TemplateLogger logger)
    {
        logger.AddLog("ExtractContentInParentheses", "Executing ExtractContentInParentheses", DocumentTemplateUtil.TemplateLogType.INFO);
        var startIndex = input.IndexOf('(');
        if (startIndex == -1)
        {
            return string.Empty; // No opening parenthesis found
        }
        var openParentheses = 1; // We've found the first '('
        var endIndex = startIndex + 1; // Start checking after the first '('
        for (var i = startIndex + 1; i < input.Length; i++)
        {
            switch (input[i])
            {
                case '(':
                    openParentheses++;
                    break;
                case ')':
                    openParentheses--;
                    break;
            }
            if (openParentheses != 0) continue;
            endIndex = i;
            break;
        }
        return openParentheses == 0 ? input.Substring(startIndex + 1, endIndex - startIndex - 1) : string.Empty;
    }

    private static List<string> BreakArgumentStringIntoArgumentList(string argumentsStr, TemplateLogger logger)
    {
        logger.AddLog("BreakArgumentStringIntoArgumentList", $"Executing BreakArgumentStringIntoArgumentList", DocumentTemplateUtil.TemplateLogType.INFO);
        var parts = new List<string>();
        var start = 0;
        var openParentheses = 0;
        for (var i = 0; i < argumentsStr.Length; i++)
        {
            switch (argumentsStr[i])
            {
                case '(':
                    openParentheses++;
                    break;
                case ')':
                    openParentheses--;
                    break;
                // Split at commas only if not inside parentheses
                case ',' when openParentheses == 0:
                    parts.Add(argumentsStr.Substring(start, i - start).Trim());
                    start = i + 1; // Move past the comma
                    break;
            }
        }
        // Add the last part after the final comma
        if (start < argumentsStr.Length)
        {
            parts.Add(argumentsStr[start..].Trim());
        }
        // trim any additional white space
        for (var i = 0; i < parts.Count; i++)
            parts[i] = parts[i].Trim();
        return parts;
    }

    private static string GetFirstOrLastElement(string jsonArray, bool getFirst, TemplateLogger logger)
    {
        try
        {
            var doc = JsonDocument.Parse(jsonArray);
            if (doc.RootElement.ValueKind != JsonValueKind.Array)
            {
                logger.AddLog("GetFirstOrLastElement", "Input is not a valid JSON array. Returning '{}'.", DocumentTemplateUtil.TemplateLogType.ERROR);
                return "{}";
            }

            var arrayElement = doc.RootElement;
            var length = arrayElement.GetArrayLength();
            if (length == 0)
            {
                logger.AddLog("GetFirstOrLastElement", "Array is empty. Returning '{}'.", DocumentTemplateUtil.TemplateLogType.ERROR);
                return "{}";  // Explicit null for empty case
            }

            var targetElement = getFirst ? arrayElement[0] : arrayElement[length - 1];
            return targetElement.GetRawText();  // Return raw JSON string of the element
        }
        catch (Exception ex)
        {
            logger.AddLog("GetFirstOrLastElement", $"Error parsing JSON: {ex.Message}. Returning empty array.", DocumentTemplateUtil.TemplateLogType.ERROR);
            return "{}";
        }
    }

    private static bool GetVariableDataIsArray(string varData, TemplateLogger logger)
    {
        logger.AddLog("GetVariableDataIsArray", "Executing GetVariableDataIsArray", DocumentTemplateUtil.TemplateLogType.INFO);
        try
        {
            string varDataAsJson = string.IsNullOrWhiteSpace(varData) ? "{}" : varData;
            var doc = JsonDocument.Parse(varDataAsJson);
            bool isArray = doc.RootElement.ValueKind == JsonValueKind.Array;
            return doc.RootElement.ValueKind == JsonValueKind.Array;
        }
        catch (Exception ex)
        {
            return false;
        }
    }

    private static List<string> SplitArgumentsInFillPoint(string fillPoint, TemplateLogger logger)
    {
        var splitArgs = fillPoint.Split('.').ToList();
        for (int i = 0; i < splitArgs.Count; i++)
        {
            var s = splitArgs[i].Trim();
            var parenIndex = s.IndexOf('(');
            if (parenIndex >= 0)
            {
                var name = s.Substring(0, parenIndex).Trim();
                var args = s.Substring(parenIndex);
                splitArgs[i] = name + args;
            }
            else
            {
                splitArgs[i] = Regex.Replace(s, @"\s+", "");
            }
        }
        if (splitArgs.Count >= 1) return splitArgs;
        logger.AddLog("SplitArgumentsInFillPoint", $"Unable to retrieve arguments from fill point string: {fillPoint}", DocumentTemplateUtil.TemplateLogType.ERROR);
        return new List<string>();
    }

    private static string GetVariableValueFromVariableList(string variableName, Dictionary<string, string> variables, TemplateLogger logger)
    {
        logger.AddLog("GetVariableValueFromVariableList", $"Looking up variable: {variableName}. Available keys: {string.Join(", ", variables.Keys)}", DocumentTemplateUtil.TemplateLogType.INFO);
        if (variables.TryGetValue(variableName, out var variableData))
        {
            return string.IsNullOrWhiteSpace(variableData) ? string.Empty : variableData;
        }
        logger.AddLog("GetVariableValueFromVariableList", $"Unable to find variable with name {variableName}", DocumentTemplateUtil.TemplateLogType.ERROR);
        return string.Empty;
    }

    private static string GetFieldValueFromVariable(string fieldName, string variableValue, TemplateLogger logger)
    {
        if (string.IsNullOrWhiteSpace(variableValue))
        {
            logger.AddLog("GetFieldValueFromVariable", $"Unable to get field value for {fieldName}, Variable Value is null or empty", DocumentTemplateUtil.TemplateLogType.ERROR);
            return string.Empty;
        }
        try
        {
            var data = JsonSerializer.Deserialize<Dictionary<string, object>>(variableValue);
            if (data == null)
            {
                logger.AddLog("GetFieldValueFromVariable", $"Unable to deserialize variable value for {fieldName}", DocumentTemplateUtil.TemplateLogType.ERROR);
                return string.Empty;
            }
            data = ConvertDictToCaseInsensitiveDict(data);
            if (data.TryGetValue(fieldName, out var value))
            {
                return value?.ToString() ?? string.Empty;
            }
            logger.AddLog("GetFieldValueFromVariable", $"Field {fieldName} not found in variable data", DocumentTemplateUtil.TemplateLogType.ERROR);
            return string.Empty;
        }
        catch (Exception ex)
        {
            logger.AddLog("GetFieldValueFromVariable", $"Error deserializing variable value for {fieldName}: {ex.Message}", DocumentTemplateUtil.TemplateLogType.ERROR);
            return string.Empty;
        }
    }

    private static GetDataCommand ParseGetDataArgString(string getDataArgStr, TemplateLogger logger)
    {
        // example expected inputs
        // @CaseAssignment.GetByQuery("CaseID":@[builtin.CaseID], "type": "ATTY")
        // @Case.GetByID(@[builtin.CaseID])
        // @Case.GetByID(2)
        logger.AddLog("ParseGetDataArgString", "Executing ParseGetDataArgString", DocumentTemplateUtil.TemplateLogType.INFO);
        getDataArgStr = RemoveAtSignFromBeginningOfString(getDataArgStr, logger);
        var getDataCommand = new GetDataCommand()
        {
            Entity = GetEntityFromCreateVarString(getDataArgStr, logger),
            Method = GetMethodFromCreateVarString(getDataArgStr, logger),
        };
        getDataCommand.Filters = GetFilterDataFromCreateVarString(getDataArgStr, getDataCommand.Method, logger);
        return getDataCommand;
    }

    private static string GetEntityFromCreateVarString(string createVarString, TemplateLogger logger)
    {
        logger.AddLog("GetEntityFromCreateVarString", $"Executing GetEntityFromCreateVarString", DocumentTemplateUtil.TemplateLogType.INFO);
        var dotIndex = createVarString.IndexOf('.');
        return dotIndex != -1 ? createVarString.Substring(0, dotIndex) : string.Empty;
    }

    private static string GetMethodFromCreateVarString(string createVarString, TemplateLogger logger)
    {
        logger.AddLog("GetMethodFromCreateVarString", $"Executing GetMethodFromCreateVarString", DocumentTemplateUtil.TemplateLogType.INFO);
        var dotIndex = createVarString.IndexOf('.');
        var openParenIndex = createVarString.IndexOf('(', dotIndex + 1);
        return openParenIndex != -1 ? createVarString.Substring(dotIndex + 1, openParenIndex - dotIndex - 1) : string.Empty;
    }

    private static Dictionary<string, string> GetFilterDataFromCreateVarString(string createVarString, string method, TemplateLogger logger)
    {
        logger.AddLog("GetFilterDataFromCreateVarString", $"Executing GetFilterDataFromCreateVarString", DocumentTemplateUtil.TemplateLogType.INFO);
        var parameters = ExtractContentInParentheses(createVarString, logger);
        var paramDict = new Dictionary<string, string>();
        if (string.Equals(method, MethodName.GET_BY_ID, StringComparison.CurrentCultureIgnoreCase))
        {
            paramDict.Add(FieldName.ROOT_ID, parameters);
        }
        else
        {
            var splitParameters = parameters.Split(',');
            foreach (var pair in splitParameters)
            {
                var pairSplit = pair.Split(':');
                var key = pairSplit[0].Trim().Trim('"');
                var value = pairSplit[1].Trim().Trim('"');
                paramDict.Add(key, value);
            }
        }
        return paramDict;
    }

    private static string RemoveBrackets(string input, TemplateLogger logger)
    {
        logger.AddLog("RemoveBrackets", $"Executing RemoveBrackets", DocumentTemplateUtil.TemplateLogType.INFO);
        // Check if the string starts with @[ and ends with ]
        if (input.StartsWith("@[") && input.EndsWith("]"))
        {
            // Remove the @[ at the beginning and ] at the end
            return input.Substring(2, input.Length - 3);
        }
        return input; // Return original if not matching the pattern
    }

    private static string RemoveAtSignFromBeginningOfString(string input, TemplateLogger logger)
    {
        logger.AddLog("RemoveAtSignFromBeginningOfString", $"Executing RemoveAtSignFromBeginningOfString", DocumentTemplateUtil.TemplateLogType.INFO);
        return input.StartsWith("@") ? input[1..] : input;
    }

    private static bool IsJsonArrayWithObjects(string jsonString)
    {
        try
        {
            using var doc = JsonDocument.Parse(jsonString);
            return doc.RootElement.ValueKind == JsonValueKind.Array &&
                   doc.RootElement.EnumerateArray().Any(element => element.ValueKind == JsonValueKind.Object);
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private async Task<string> GetVariableData(string getDataArg, ProcessedTemplate processedTemplate, TemplateLogger logger)
    {
        logger.AddLog("GetVariableData", $"Getting variable data with arguments: {getDataArg}.", DocumentTemplateUtil.TemplateLogType.INFO);
        var command = ParseGetDataArgString(getDataArg, logger);
        command = await FillFilterWithVariableValues(command, processedTemplate, logger);
        command = FillRootIdIfNeeded(command, processedTemplate, logger);
        string value;
        try
        {
            value = await _dataProviderService.GetDocumentVariableValue(command, processedTemplate.UseResource);
        }
        catch (Exception e)
        {
            logger.AddLog("GetVariableData", e.Message, DocumentTemplateUtil.TemplateLogType.ERROR);
            value = string.Empty;
        }
        return value;
    }

    private async Task<GetDataCommand> FillFilterWithVariableValues(GetDataCommand command, ProcessedTemplate processedTemplate, TemplateLogger logger)
    {
        // expected values input example: "CaseID":@[builtin.CaseID], "type": "ATTY", "CaseID":[@[builtin.CaseID]]
        // expected values result example: "CaseID":2, "type": "ATTY", "CaseID":"[2]
        foreach (var (key, value) in command.Filters.Where(filter => filter.Value.Contains("@[")))
        {
            if (value.StartsWith("["))
            {
                // fillpoint is in array
                var startIndex = value.IndexOf("@[", StringComparison.Ordinal);
                var endIndex = value.IndexOf("]", StringComparison.Ordinal);
                var fillpoint = value.Substring(startIndex, endIndex - startIndex + 1);
                var filledValue = await GetFillPointValue(fillpoint, processedTemplate, logger);
                command.Filters[key] = value.Replace(fillpoint, filledValue);
            }
            else
            {
                command.Filters[key] = await GetFillPointValue(value, processedTemplate, logger);
            }
        }
        return command;
    }

    private static GetDataCommand FillRootIdIfNeeded(GetDataCommand command, ProcessedTemplate processedTemplate, TemplateLogger logger)
    {
        if (string.Equals(command.Method, MethodName.GET_BY_ID, StringComparison.OrdinalIgnoreCase) == false)
        {
            logger.AddLog("FillRootIdIfNeeded", $"RootID not needed got method {command.Method}, skipping", DocumentTemplateUtil.TemplateLogType.INFO);
            return command;
        }
        logger.AddLog("FillRootIdIfNeeded", "Filling RootID", DocumentTemplateUtil.TemplateLogType.INFO);
        if (command.Filters.TryGetValue(FieldName.ROOT_ID, out var rootid) && int.TryParse(rootid, out var id))
        {
            command.RootID = id;
            logger.AddLog("FillRootIdIfNeeded", $"RootID Set: {command.RootID}", DocumentTemplateUtil.TemplateLogType.INFO);
        }
        else
        {
            logger.AddLog("FillRootIdIfNeeded", "Unable to retrieve rootID value for GetByID method.", DocumentTemplateUtil.TemplateLogType.ERROR);
        }
        return command;
    }

    private static int GetNextVarIndex(StringBuilder templateText, TemplateLogger logger)
    {
        logger.AddLog("GetNextVarIndex", $"Executing GetNextVarIndex", DocumentTemplateUtil.TemplateLogType.INFO);
        return templateText.ToString().IndexOf(VariableStartString, StringComparison.Ordinal);
    }

    private static string GetFullVariableString(StringBuilder templateText, int index, TemplateLogger logger)
    {
        logger.AddLog("GetFullVariableString", $"Executing GetFullVariableString with index {index}", DocumentTemplateUtil.TemplateLogType.INFO);
        if (index < 0 || index >= templateText.Length)
        {
            logger.AddLog("GetFullVariableString", $"Invalid start index: {index}, template length: {templateText.Length}", DocumentTemplateUtil.TemplateLogType.ERROR);
            throw new PineTemplateGeneratorException($"Start index cannot be less than 0 or greater than input length. (Parameter 'startat')");
        }
        var text = templateText.ToString();
        var endIndex = GetVariableEndIndex(index, text, logger);
        if (endIndex < 0 || endIndex >= text.Length)
        {
            logger.AddLog("GetFullVariableString", $"Invalid end index: {endIndex}, template length: {text.Length}", DocumentTemplateUtil.TemplateLogType.ERROR);
            throw new PineTemplateGeneratorException($"Cannot find variable ending in template at index {index}");
        }
        int length = endIndex - index + 1;
        if (length <= 0)
        {
            logger.AddLog("GetFullVariableString", $"Invalid substring length: {length} at index: {index}", DocumentTemplateUtil.TemplateLogType.ERROR);
            throw new PineTemplateGeneratorException($"Invalid substring length in GetFullVariableString at index {index}");
        }
        var result = text.Substring(index, length);
        logger.AddLog("GetFullVariableString", $"Extracted variable: {result}", DocumentTemplateUtil.TemplateLogType.INFO);
        return result;
    }

    private static int GetVariableEndIndex(int startIndex, string templateText, TemplateLogger logger)
    {
        logger.AddLog("GetVariableEndIndex", $"Executing GetVariableEndIndex with startIndex {startIndex}", DocumentTemplateUtil.TemplateLogType.INFO);
        if (startIndex + 2 >= templateText.Length)
        {
            logger.AddLog("GetVariableEndIndex", $"Start index {startIndex} too close to end of template text (length: {templateText.Length})", DocumentTemplateUtil.TemplateLogType.ERROR);
            return -1;
        }
        var openBrackets = 1;
        var endIndex = -1;
        for (var i = startIndex + 2; i < templateText.Length; i++)
        {
            switch (templateText[i])
            {
                case '[':
                    openBrackets++;
                    break;
                case ']':
                    openBrackets--;
                    break;
            }
            if (openBrackets == 0)
            {
                endIndex = i;
                break;
            }
        }
        if (endIndex == -1)
        {
            logger.AddLog("GetVariableEndIndex", $"No matching closing bracket found for startIndex {startIndex}", DocumentTemplateUtil.TemplateLogType.ERROR);
        }
        else
        {
            logger.AddLog("GetVariableEndIndex", $"Found end index: {endIndex}", DocumentTemplateUtil.TemplateLogType.INFO);
        }
        return endIndex;
    }

    private static Dictionary<string, string> ConvertDictToCaseInsensitiveDict(Dictionary<string, string> origDict)
    {
        var newDict = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var kvp in origDict)
        {
            newDict[kvp.Key] = kvp.Value;
        }
        return newDict;
    }

    private static int GetIndexOfDotAfterFinalParenthesis(string input)
    {
        // Find the last closing parenthesis
        var lastParenIndex = input.LastIndexOf(')');
        // If no closing parenthesis is found, return false
        if (lastParenIndex == -1)
            return -1;
        // Check if a '.' exists immediately after the last parenthesis
        if (lastParenIndex < input.Length - 1 && input[lastParenIndex + 1] == '.')
            return lastParenIndex + 1;
        return -1;
    }

    private static Dictionary<string, object> ConvertDictToCaseInsensitiveDict(Dictionary<string, object> origDict)
    {
        var newDict = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
        foreach (var kvp in origDict)
        {
            newDict[kvp.Key] = kvp.Value;
        }
        return newDict;
    }

    public static bool HasBalancedParentheses(string input)
    {
        int balance = 0;
        foreach (char c in input)
        {
            if (c == '(') balance++;
            if (c == ')') balance--;
            if (balance < 0) return false; // More closing than opening
        }
        return balance == 0; // Must be fully balanced
    }

    private static string RemoveReturnsFromString(string input)
    {
        if (string.IsNullOrEmpty(input))
            return input;
        return input.Replace("\r\n", "").Replace("\r", "").Replace("\n", "");
    }

    // helper methods finish
    //Image helper functions
    //begin image helper functions
    private Dictionary<string, string> ExtractImages(StringBuilder templateText, TemplateLogger logger)
    {
        logger.AddLog("ExtractImages", $"Executing ExtractImages", DocumentTemplateUtil.TemplateLogType.INFO);
        var imageDict = new Dictionary<string, string>();
        var rtfText = templateText.ToString(); // Temporary string for regex matching
        // Get matches and sort in reverse order by index to avoid shifts during replacement
        var matches = RtfRegularExpressions.PictureRegex.Matches(rtfText).Cast<Match>().OrderByDescending(m => m.Index).ToList();
        int imageIndex = 0;
        foreach (var match in matches)
        {
            var placeholder = $"[IMAGE_PLACEHOLDER_{imageIndex}]";
            imageDict[placeholder] = match.Value;
            // Replace directly in the StringBuilder
            templateText.Remove(match.Index, match.Length);
            templateText.Insert(match.Index, placeholder);
            imageIndex++;
        }
        return imageDict;
    }

    private void RestoreImages(StringBuilder templateText, Dictionary<string, string> imageDict, TemplateLogger logger)
    {
        logger.AddLog("RestoreImages", $"Executing RestoreImages", DocumentTemplateUtil.TemplateLogType.INFO);
        foreach (var kvp in imageDict)
        {
            templateText.Replace(kvp.Key, kvp.Value);
        }
    }

    private string ConvertDocumentFontsColors(string doc, int maxFontNumber, int maxColorNumber)
    {
        var fontMatches = RtfRegularExpressions.Font.Matches(doc)
            .Cast<Match>()
            .Where(m => !RtfRegularExpressions.FontTable.Match(doc).Value.Contains(m.Value))
            .OrderByDescending(m => m.Index);
        foreach (var match in fontMatches)
        {
            int fontNum = int.Parse(match.Value.Substring(2));
            doc = doc.Remove(match.Index, match.Length).Insert(match.Index, $"\\f{fontNum + maxFontNumber}");
        }
        var colorMatches = RtfRegularExpressions.DocumentColors.Matches(doc)
            .Cast<Match>()
            .Where(m => !RtfRegularExpressions.ColorTable.Match(doc).Value.Contains(m.Value))
            .OrderByDescending(m => m.Index);
        foreach (var match in colorMatches)
        {
            string prefix = match.Value switch
            {
                _ when match.Value.StartsWith("\\highlight") => "\\highlight",
                _ when match.Value.StartsWith("\\cb") => "\\cb",
                _ when match.Value.StartsWith("\\cf") => "\\cf",
                _ when match.Value.StartsWith("\\brdrcf") => "\\brdrcf",
                _ when match.Value.StartsWith("\\clcbpatraw") => "\\clcbpatraw",
                _ when match.Value.StartsWith("\\clcbpat") => "\\clcbpat",
                _ => match.Value
            };
            int colorNum = int.Parse(match.Value.Substring(prefix.Length));
            doc = doc.Remove(match.Index, match.Length).Insert(match.Index, $"{prefix}{colorNum + maxColorNumber}");
        }
        return doc;
    }

    private int ParseFontTable(string fontTable)
    {
        var matches = RtfRegularExpressions.TableItem.Matches(fontTable);
        int max = -1;
        foreach (Match match in matches)
        {
            int idx = match.Value.IndexOf("\\f");
            while (idx >= 0 && !char.IsDigit(match.Value[idx + 2])) idx = match.Value.IndexOf("\\f", idx + 2);
            if (idx >= 0)
            {
                int numStart = idx + 2;
                int numEnd = numStart;
                while (numEnd < match.Value.Length && char.IsDigit(match.Value[numEnd])) numEnd++;
                int num = int.Parse(match.Value.Substring(numStart, numEnd - numStart));
                if (num > max) max = num;
            }
        }
        return max + 1;
    }

    private int ParseColorTable(string colorTable)
    {
        return new Regex(";").Matches(colorTable).Count - 1;
    }

    //end image helper functions
    // Chained Functions Start
    // field chained functions
    public static class DateFormat
    {
        public static string Run(string dateStr, string format)
        {
            if (string.IsNullOrEmpty(dateStr))
                return string.Empty;
            if (DateTime.TryParse(dateStr, out var date) == false)
                throw new PineTemplateGeneratorException(DocumentGeneratorErrorCodes.UNABLE_TO_PARSE);
            return format.ToLowerInvariant() switch
            {
                TemplateFunctionVariants.RayDate1 => date.ToString(StringUtil.RayDate1),
                TemplateFunctionVariants.RayDate2 => FormatDateWithOrdinal(date),
                TemplateFunctionVariants.RayDate3 => date.ToString(StringUtil.RayDate3),
                TemplateFunctionVariants.RayDate4 => date.ToString(StringUtil.RayDate4),
                TemplateFunctionVariants.RayDate5 => date.ToString(StringUtil.RayDate5),
                TemplateFunctionVariants.RayDate6 => date.ToString(StringUtil.RayDate6),
                _ => date.ToString(format)
            };
        }

        private static string FormatDateWithOrdinal(DateTime date)
        {
            var day = date.Day;
            var ordinalSuffix = GetOrdinalSuffix(day);
            return $"{day}{ordinalSuffix} day of {date:MMMM}, {date:yyyy}";
        }

        private static string GetOrdinalSuffix(int day)
        {
            if (day is >= 11 and <= 13) return "th"; // Special case for 11th, 12th, 13th
            return (day % 10) switch
            {
                1 => "st",
                2 => "nd",
                3 => "rd",
                _ => "th"
            };
        }
    }

    public static class IsNullOrEmpty
    {
        public static string Run(string? value)
        {
            if (value is null)
            {
                return "true";
            }
            return string.IsNullOrEmpty(value) ? "true" : "false";
        }
    }

    public static class SetCasing
    {
        public static string Run(string str, string casing)
        {
            if (DocumentTemplateUtil.CasingOptions.ListAll.Contains(casing.ToLower()) == false)
                throw new PineTemplateGeneratorException(DocumentGeneratorErrorCodes.UNKNOWN_VARIABLE_OPTION,
                    $"Error in in SetCasing function | String: {str}, Casing: {casing}");
            switch (casing.ToLower())
            {
                case DocumentTemplateUtil.CasingOptions.TITLE_CASE:
                    var textInfo = CultureInfo.CurrentCulture.TextInfo;
                    return textInfo.ToTitleCase(str);
                case DocumentTemplateUtil.CasingOptions.LOWER_CASE:
                    return str.ToLower();
                case DocumentTemplateUtil.CasingOptions.UPPER_CASE:
                    return str.ToUpper();
                case DocumentTemplateUtil.CasingOptions.SENTENCE_CASE:
                    return GetSentenceCase(str);
            }
            return str;
        }

        private static string GetSentenceCase(string text)
        {
            if (string.IsNullOrWhiteSpace(text))
                return string.Empty;
            var lowerCase = text.ToLower();
            var textInfo = CultureInfo.CurrentCulture.TextInfo;
            var sentences = lowerCase.Split(new[] { '.', '!', '?' }, StringSplitOptions.RemoveEmptyEntries);
            for (var i = 0; i < sentences.Length; i++)
            {
                sentences[i] = sentences[i].TrimStart();
                if (sentences[i].Length > 0)
                {
                    sentences[i] = textInfo.ToTitleCase(sentences[i][0].ToString()) + sentences[i].Substring(1);
                }
            }
            return string.Join(". ", sentences) + (text.EndsWith(".") ? "." : "");
        }
    }

    public static class GetStringSegment
    {
        public static string Run(string str, string segment, int length)
        {
            if (DocumentTemplateUtil.StringSegmentOptions.ListAll.Contains(segment.ToLower()) == false)
                throw new PineTemplateGeneratorException(DocumentGeneratorErrorCodes.UNKNOWN_VARIABLE_OPTION,
                    $"Error in in GetStringSegment function | String: {str}, Segment: {segment}");

            if (str.Length < length)
            {
                return str;
            }
            switch (segment.ToLower())
            {
                case DocumentTemplateUtil.StringSegmentOptions.FIRST:
                    return str.Substring(0, length);
                case DocumentTemplateUtil.StringSegmentOptions.LAST:
                    return str.Substring(str.Length - length, length);
            }
            return str;
        }
    }

    private static class GetHeShe
    {
        public static string Run(string genderCode)
        {
            throw new NotImplementedException();
        }
    }

    private static class GetHimHer
    {
        public static string Run(string genderCode)
        {
            return genderCode.ToLower() switch
            {
                "m" => "him",
                "f" => "her",
                _ => "him/her"
            };
        }
    }

    private static class GetHisHer
    {
        public static string Run(string genderCode)
        {
            return genderCode.ToLower() switch
            {
                "m" => "his",
                "f" => "her",
                _ => "his/her"
            };
        }
    }

    private static class GetHimselfHerself
    {
        public static string Run(string genderCode)
        {
            return genderCode.ToLower() switch
            {
                "m" => "himself",
                "f" => "herself",
                _ => "himself/herself"
            };
        }
    }

    private static class GetMrMrs
    {
        public static string Run(string genderCode)
        {
            return genderCode.ToLower() switch
            {
                "m" => "Mr.",
                "f" => "Mrs.",
                _ => "Mr./Mrs"
            };
        }
    }

    // object chained functions
    public static class FormatName
    {
        public static string Run(string data, string args)
        {
            if (string.IsNullOrEmpty(data) || data == "{}")
            {
                return string.Empty;
            }
            return NameUtil.FormatName(data, args);
        }
    }

    // array chained functions
    private static class JsonArrayHasElements
    {
        public static string Run(string data)
        {
            try
            {
                // Parse the JSON string into a JsonDocument
                using var doc = JsonDocument.Parse(data);
                // Check if the root element is a JSON array
                if (doc.RootElement.ValueKind != JsonValueKind.Array)
                    return "false";
                if (doc.RootElement.GetArrayLength() == 0)
                    return "false";
            }
            catch
            {
                // Return false if parsing fails (not a valid JSON array)
                return "false";
            }
            return "true";
        }
    }

    private static string Normalize(string s)
    {
        return Regex.Replace(s, @"\s+", "").ToLowerInvariant();
    }

    public static class FormatNumber
    {
        public static string Run(string fieldInput, string mask)
        {
            // Extract only digits from the input
            string digits = new string(fieldInput.Where(char.IsDigit).ToArray());
            // Count the number of '#' placeholders in the mask
            int requiredDigits = mask.Count(c => c == '#');
            if (digits.Length < requiredDigits)
            {
                return fieldInput;
                //for now, we will just return the input without the mask.
                //throw new ArgumentException($"Input must contain at least {requiredDigits} digits to match the mask.");
            }
            // Build the formatted string
            StringBuilder formatted = new StringBuilder();
            int digitIndex = 0;
            foreach (char c in mask)
            {
                if (c == '#')
                {
                    formatted.Append(digits[digitIndex++]);
                }
                else
                {
                    formatted.Append(c);
                }
            }
            // Append any remaining digits
            if (digitIndex < digits.Length)
            {
                formatted.Append(digits.Substring(digitIndex));
            }
            return formatted.ToString();
        }
    }

    public static class FormatPhoneNumber
    {
        public static string Run(string fieldInput)
        {
            return StringUtil.GetPhoneString(fieldInput);
        }
    }
}