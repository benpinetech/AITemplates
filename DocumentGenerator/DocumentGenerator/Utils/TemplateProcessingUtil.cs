using DocumentGenerator.Models;
using PineCone.BuildingBlocks.Data.Constants;
using PineCone.BuildingBlocks.Data.Resources;
using PineCone.BuildingBlocks.SharedUtilities;
using System.Text.Json;
using System.Text.RegularExpressions;
using static DocumentGenerator.DocumentGeneratorService;
using static PineCone.BuildingBlocks.Data.Results.DocumentTemplateWithCollectionsResult;
namespace DocumentGenerator.Utils
{
    public static class TemplateProcessingUtil
    {
        public static async Task<Dictionary<string, string>> ProcessTemplateVariables(List<TemplateVariableResult> templateVariables, Dictionary<string, string> variableDict, DataProviderService dataProviderService, bool useResource = true, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Starting ProcessTemplateVariables with {0} variables.", templateVariables.Count);
            foreach (var variable in templateVariables)
            {
                if (debugEnabled) Console.WriteLine("Processing variable: Name={0}, SystemEntityID={1}, Arguments={2}", variable.Name, variable.SystemEntityID, variable.Arguments);
                if (variable.SystemEntityID == -1)
                {
                    variableDict.Add(variable.Name, variable.Arguments);
                    if (debugEnabled) Console.WriteLine("Added static variable {0} with value: {1}", variable.Name, variable.Arguments);
                }
                else
                {
                    string varData;
                    try
                    {
                        var entityName = TemplateEntities.TemplateEntityNamesToIds().FirstOrDefault(x => x.Value == (int)variable.SystemEntityID).Key ?? "Unknown";
                        if (debugEnabled) Console.WriteLine("Fetching data for entity: {0}", entityName);
                        varData = await GetVariableData(variable.Arguments, entityName, dataProviderService, useResource, variableDict, debugEnabled);
                        if (string.IsNullOrWhiteSpace(varData) || varData == "{}")
                        {
                            varData = "[]";
                            if (debugEnabled) Console.WriteLine("VarData for {0} was empty or {{}}, setting to [].", variable.Name);
                        }
                        else
                        {
                            if (debugEnabled) Console.WriteLine("VarData for {0} retrieved: {1}", variable.Name, varData);
                        }
                    }
                    catch (Exception ex)
                    {
                        if (debugEnabled) Console.WriteLine("Exception in GetVariableData for {0}: {1}", variable.Name, ex.Message);
                        varData = "[]";
                    }
                    variableDict.Add(variable.Name, varData);
                    if (debugEnabled) Console.WriteLine("Added variable {0} with value: {1}", variable.Name, varData);
                }
            }
            if (debugEnabled) Console.WriteLine("Finished ProcessTemplateVariables. VariableDict now has {0} entries.", variableDict.Count);
            return variableDict;
        }
        
        public static async Task<string> ProcessPromptData(TemplatePromptConfiguration prompt, Dictionary<string, string> variableDict, DataProviderService dataProviderService, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Starting ProcessPromptData for prompt: Title={0}, Type={1}, Entity={2}, Filter={3}", prompt.Title, prompt.Type, prompt.Entity, prompt.Filter);
            if (prompt.Type != TemplatePromptType.SELECT_MANY && prompt.Type != TemplatePromptType.SELECT_SINGLE)
            {
                if (debugEnabled) Console.WriteLine("Skipping: Prompt type is not SELECT_MANY or SELECT_SINGLE.");
                return string.Empty;
            }
            if (string.IsNullOrEmpty(prompt.Entity))
            {
                if (debugEnabled) Console.WriteLine("Error: No entity specified.");
                return string.Empty;
            }
            string filter = prompt.Filter ?? string.Empty;
            string resolvedFilter = await ResolveFilterString(prompt.Entity, filter, variableDict, debugEnabled);
            if (debugEnabled) Console.WriteLine("Resolved filter: {0}", resolvedFilter);
            string fullCommand = $"@{prompt.Entity}.GetByQuery({resolvedFilter})";
            if (debugEnabled) Console.WriteLine("Full command: {0}", fullCommand);
            string commandData;
            try
            {
                commandData = await dataProviderService.GetDocumentVariableValue(fullCommand, variableDict, true);
                if (debugEnabled) Console.WriteLine("Fetched commandData: {0}", commandData);
            }
            catch (Exception ex)
            {
                if (debugEnabled) Console.WriteLine("Exception fetching data: {0}", ex.Message);
                return string.Empty;
            }
            return commandData;
        }
        
        public static async Task<Dictionary<string, string>> ProcessPromptInputs(List<TemplatePromptConfiguration> templatePrompts, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Starting ProcessPromptInputs with {0} prompts.", templatePrompts.Count);
            Dictionary<string, string> prompts = new Dictionary<string, string>();
            foreach (var prompt in templatePrompts)
            {
                if (debugEnabled) Console.WriteLine("Processing prompt: DocumentVariableName={0}, Type={1}, Value={2}", prompt.DocumentVariableName, prompt.Type, prompt.Value);
                if (prompt.Type == TemplatePromptType.TEXT ||
                    prompt.Type == TemplatePromptType.DATE ||
                    prompt.Type == TemplatePromptType.DATETIME)
                {
                    prompts.Add(prompt.DocumentVariableName, prompt.Value);
                    if (debugEnabled) Console.WriteLine("Added text/date prompt {0}: {1}", prompt.DocumentVariableName, prompt.Value);
                    continue;
                }
                if (prompt.Type == TemplatePromptType.SELECT_SINGLE)
                {
                    var selectedData = prompt.ParsedData.FirstOrDefault(data => data.IsSelected);
                    if (selectedData is not null)
                    {
                        var serialized = JsonSerializer.Serialize(selectedData.Data);
                        prompts.Add(prompt.DocumentVariableName, serialized);
                        if (debugEnabled) Console.WriteLine("Added select_single prompt {0}: {1}", prompt.DocumentVariableName, serialized);
                    }
                    else
                    {
                        if (debugEnabled) Console.WriteLine("No selected data for select_single prompt {0}", prompt.DocumentVariableName);
                    }
                    continue;
                }
                if (prompt.Type == TemplatePromptType.SELECT_MANY)
                {
                    var selectedData = prompt.ParsedData.Where(data => data.IsSelected).Select(data => data.Data).ToList();
                    var serialized = JsonSerializer.Serialize(selectedData);
                    prompts.Add(prompt.DocumentVariableName, serialized);
                    if (debugEnabled) Console.WriteLine("Added select_many prompt {0}: {1}", prompt.DocumentVariableName, serialized);
                    continue;
                }
                if (prompt.Type == TemplatePromptType.CURRENCY)
                {
                    var promptValue = $"${String.Format(StringUtil.CurrencyTwoDecimalPlaces, prompt.Value)}";
                    prompts.Add(prompt.DocumentVariableName, promptValue);
                    if (debugEnabled) Console.WriteLine("Added currency prompt {0}: {1}", prompt.DocumentVariableName, promptValue);
                    continue;
                }
                if (prompt.Type == TemplatePromptType.RADIO_SELECT)
                {
                    var selectedData = prompt.ParsedData.FirstOrDefault(data => data.IsSelected);
                    var selectedValue = string.Empty;
                    if (selectedData != null)
                    {
                        foreach (KeyValuePair<string, object> kvp in selectedData.Data)
                        {
                            if (kvp.Value is string value)
                                selectedValue = value;
                        }
                    }
                    if (!string.IsNullOrEmpty(selectedValue))
                    {
                        prompts.Add(prompt.DocumentVariableName, selectedValue);
                        if (debugEnabled) Console.WriteLine("Added radio_select prompt {0}: {1}", prompt.DocumentVariableName, selectedValue);
                    }
                    else
                    {
                        if (debugEnabled) Console.WriteLine("No selected value for radio_select prompt {0}", prompt.DocumentVariableName);
                    }
                    continue;
                }
                if (prompt.Type == TemplatePromptType.CHECKBOX)
                {
                    var selectedData = prompt.ParsedData.Where(data => data.IsSelected).Select(data => data.Data).ToList();
                    var serialized = JsonSerializer.Serialize(selectedData);
                    prompts.Add(prompt.DocumentVariableName, serialized);
                    if (debugEnabled) Console.WriteLine("Added checkbox prompt {0}: {1}", prompt.DocumentVariableName, serialized);
                    continue;
                }
                if (debugEnabled) Console.WriteLine("Unhandled prompt type {0} for {1}", prompt.Type, prompt.DocumentVariableName);
            }
            if (debugEnabled) Console.WriteLine("Finished ProcessPromptInputs. Prompts dictionary has {0} entries.", prompts.Count);
            return prompts;
        }

        public static async Task<string> GetVariableData(string getDataArg, string entity, DataProviderService dataProviderService, bool useResource, Dictionary<string, string> variableList, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Starting GetVariableData: getDataArg={0}, entity={1}, useResource={2}", getDataArg, entity, useResource);
            var command = ParseGetDataArgString(getDataArg, entity, debugEnabled);
            if (debugEnabled) Console.WriteLine("Parsed command: Entity={0}, Method={1}, Filters={2}", command.Entity, command.Method, JsonSerializer.Serialize(command.Filters));
            command = await FillFilterWithVariableValues(command, dataProviderService, variableList, debugEnabled);
            if (debugEnabled) Console.WriteLine("After FillFilterWithVariableValues: Filters={0}", JsonSerializer.Serialize(command.Filters));
            command = FillRootIdIfNeeded(command, debugEnabled);
            if (debugEnabled) Console.WriteLine("After FillRootIdIfNeeded: RootID={0}", command.RootID);
            string value;
            try
            {
                value = await dataProviderService.GetDocumentVariableValue(command, useResource);
                if (debugEnabled) Console.WriteLine("DataProviderService returned: {0}", value);
            }
            catch (Exception ex)
            {
                if (debugEnabled) Console.WriteLine("Exception in GetDocumentVariableValue: {0}", ex.Message);
                value = string.Empty;
            }
            if (debugEnabled) Console.WriteLine("GetVariableData returning: {0}", value);
            return value;
        }
        private static GetDataCommand ParseGetDataArgString(string getDataArgStr, string entity, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Parsing getDataArgStr: {0}, entity={1}", getDataArgStr, entity);
            var getDataCommand = new GetDataCommand()
            {
                Entity = entity,
                Method = GetMethodFromCreateVarString(getDataArgStr, debugEnabled)
            };
            getDataCommand.Filters = GetFilterDataFromCreateVarString(getDataArgStr, getDataCommand.Method, getDataCommand.Entity, debugEnabled);
            if (debugEnabled) Console.WriteLine("Parsed filters: {0}", JsonSerializer.Serialize(getDataCommand.Filters));
            return getDataCommand;
        }
        private static async Task<GetDataCommand> FillFilterWithVariableValues(GetDataCommand command, DataProviderService dataProviderService, Dictionary<string, string> variableList, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Starting FillFilterWithVariableValues for command.Entity={0}, Method={1}", command.Entity, command.Method);
            foreach (var (key, value) in command.Filters.Where(filter => filter.Value.Contains("@[")))
            {
                if (debugEnabled) Console.WriteLine("Processing filter: Key={0}, Value={1}", key, value);
                if (value.StartsWith("["))
                {
                    var startIndex = value.IndexOf("@[", StringComparison.Ordinal);
                    var endIndex = value.IndexOf("]", StringComparison.Ordinal);
                    var fillpoint = value.Substring(startIndex, endIndex - startIndex + 1);
                    if (debugEnabled) Console.WriteLine("Extracted fillpoint: {0}", fillpoint);
                    var filledValue = await GetFillPointValue(fillpoint, variableList, debugEnabled);
                    if (debugEnabled) Console.WriteLine("Filled value: {0}", filledValue);
                    command.Filters[key] = value.Replace(fillpoint, filledValue);
                    if (debugEnabled) Console.WriteLine("Updated filter: Key={0}, New Value={1}", key, command.Filters[key]);
                }
                else
                {
                    var filledValue = await GetFillPointValue(value, variableList, debugEnabled);
                    if (debugEnabled) Console.WriteLine("Filled value for direct fillpoint: {0}", filledValue);
                    command.Filters[key] = filledValue;
                }
            }
            if (debugEnabled) Console.WriteLine("Finished FillFilterWithVariableValues. Updated filters: {0}", JsonSerializer.Serialize(command.Filters));
            return command;
        }
        private static GetDataCommand FillRootIdIfNeeded(GetDataCommand command, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Checking FillRootIdIfNeeded for Method={0}", command.Method);
            if (string.Equals(command.Method, "GetById", StringComparison.OrdinalIgnoreCase) == false)
            {
                if (debugEnabled) Console.WriteLine("Not GetById, skipping.");
                return command;
            }
            if (command.Filters.TryGetValue("RootID", out var rootid) && int.TryParse(rootid, out var id))
            {
                command.RootID = id;
                if (debugEnabled) Console.WriteLine("Set RootID to {0}", id);
            }
            else
            {
                if (debugEnabled) Console.WriteLine("No valid RootID found.");
            }
            return command;
        }
        private static string GetMethodFromCreateVarString(string createVarString, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Extracting method from: {0}", createVarString);
            var dotIndex = createVarString.IndexOf('.');
            var openParenIndex = createVarString.IndexOf('(');
            string methodType;
            if (dotIndex == -1 || dotIndex >= openParenIndex)
            {
                methodType = createVarString.Substring(0, openParenIndex);
                if (debugEnabled) Console.WriteLine($"No prefix dot or dot after '(', using method from start: {methodType}");
            }
            else
            {
                methodType = createVarString.Substring(dotIndex + 1, openParenIndex - dotIndex - 1);
                if (debugEnabled) Console.WriteLine($"Prefix dot found, method: {methodType}");
            }
            return methodType;
        }
        private static Dictionary<string, string> GetFilterDataFromCreateVarString(string createVarString, string method, string entity, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Extracting filters from: {0}, method={1}, entity={2}", createVarString, method, entity);
            var parameters = ExtractContentInParentheses(createVarString, debugEnabled);
            if (debugEnabled) Console.WriteLine("Extracted parameters: {0}", parameters);
            var paramDict = new Dictionary<string, string>();
            if (string.Equals(method, "GetById", StringComparison.CurrentCultureIgnoreCase))
            {
                paramDict.Add("RootID", parameters);
                if (debugEnabled) Console.WriteLine("Added RootID: {0}", parameters);
            }
            else
            {
                var splitParameters = parameters.Split(',');
                foreach (var pair in splitParameters)
                {
                    if (debugEnabled) Console.WriteLine("Processing pair: {0}", pair);
                    var pairSplit = pair.Split(':');
                    var key = pairSplit[0].Trim().Trim('"');
                    var value = pairSplit[1].Trim().Trim('"');
                    paramDict.Add(key, value);
                    if (debugEnabled) Console.WriteLine("Added filter: {0}={1}", key, value);
                }
            }
            return paramDict;
        }
        private static async Task<string> GetFillPointValue(string fillPoint, Dictionary<string, string> variableDict, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Starting GetFillPointValue for: {0}", fillPoint);
            fillPoint = RemoveBrackets(fillPoint, debugEnabled);
            if (debugEnabled) Console.WriteLine("After RemoveBrackets: {0}", fillPoint);
            var splitArgs = SplitArgumentsInFillPoint(fillPoint, debugEnabled);
            if (debugEnabled) Console.WriteLine("Split args: {0}", string.Join(", ", splitArgs));
            if (splitArgs.Any() == false)
            {
                if (debugEnabled) Console.WriteLine("No split args, returning empty.");
                return ""; // Early return for invalid fillpoints
            }
            try
            {
                var varData = GetVariableValueFromVariableList(splitArgs[0], variableDict, debugEnabled);
                if (debugEnabled) Console.WriteLine("Retrieved varData for {0}: {1}", splitArgs[0], varData);
                if (splitArgs.Count == 1)
                {
                    var result = string.IsNullOrWhiteSpace(varData) ? "" : varData;
                    if (debugEnabled) Console.WriteLine("Single arg, returning: {0}", result);
                    return result;
                }
                bool isArray = GetVariableDataIsArray(varData, debugEnabled);
                if (debugEnabled) Console.WriteLine("varData is array: {0}", isArray);
                var processed = isArray ?
                    (await ProcessFillPointArray(splitArgs, varData, debugEnabled)) :
                    (await ProcessFillPointObject(splitArgs, varData, debugEnabled));
                if (debugEnabled) Console.WriteLine("Processed result: {0}", processed);
                return processed;
            }
            catch (Exception ex)
            {
                if (debugEnabled) Console.WriteLine("Exception in GetFillPointValue: {0}", ex.Message);
                return "";
            }
        }
        // Add other methods similarly: ExtractContentInParentheses, RemoveBrackets, SplitArgumentsInFillPoint, etc.
        // For space, not pasting all, but follow the pattern from the original code.
        private static string ExtractContentInParentheses(string input, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Extracting content from parentheses in: {0}", input);
            var startIndex = input.IndexOf('(');
            if (startIndex == -1)
            {
                if (debugEnabled) Console.WriteLine("No opening parenthesis found.");
                return string.Empty;
            }
            var openParentheses = 1;
            var endIndex = startIndex + 1;
            for (var i = startIndex + 1; i < input.Length; i++)
            {
                if (input[i] == '(') openParentheses++;
                if (input[i] == ')') openParentheses--;
                if (openParentheses == 0)
                {
                    endIndex = i;
                    break;
                }
            }
            var result = openParentheses == 0 ? input.Substring(startIndex + 1, endIndex - startIndex - 1) : string.Empty;
            if (debugEnabled) Console.WriteLine("Extracted: {0}", result);
            return result;
        }
        private static string RemoveBrackets(string input, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Removing brackets from: {0}", input);
            // Check if the string starts with @[ and ends with ]
            if (input.StartsWith("@[") && input.EndsWith("]"))
            {
                // Remove the @[ at the beginning and ] at the end
                var result = input.Substring(2, input.Length - 3);
                if (debugEnabled) Console.WriteLine("Removed brackets: {0}", result);
                return result;
            }
            if (debugEnabled) Console.WriteLine("No brackets to remove.");
            return input; // Return original if not matching the pattern
        }
        private static List<string> SplitArgumentsInFillPoint(string fillPoint, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Splitting arguments in: {0}", fillPoint);
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
                    if (debugEnabled) Console.WriteLine("Processed arg {0}: {1}", i, splitArgs[i]);
                }
                else
                {
                    splitArgs[i] = Regex.Replace(s, @"\s+", "");
                    if (debugEnabled) Console.WriteLine("Processed arg {0} (no paren): {1}", i, splitArgs[i]);
                }
            }
            var result = splitArgs.Count >= 1 ? splitArgs : new List<string>();
            if (debugEnabled) Console.WriteLine("Final split args count: {0}", result.Count);
            return result;
        }
        private static string GetVariableValueFromVariableList(string variableName, Dictionary<string, string> variables, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Looking up variable: {0}", variableName);
            if (variables.TryGetValue(variableName, out var variableData))
            {
                var result = string.IsNullOrWhiteSpace(variableData) ? string.Empty : variableData;
                if (debugEnabled) Console.WriteLine("Found: {0}", result);
                return result;
            }
            if (debugEnabled) Console.WriteLine("Not found.");
            return string.Empty;
        }
        private static bool GetVariableDataIsArray(string varData, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Checking if varData is array: {0}", varData);
            try
            {
                string varDataAsJson = string.IsNullOrWhiteSpace(varData) ? "{}" : varData;
                var doc = JsonDocument.Parse(varDataAsJson);
                bool isArray = doc.RootElement.ValueKind == JsonValueKind.Array;
                if (debugEnabled) Console.WriteLine("Is array: {0}", isArray);
                return isArray;
            }
            catch (Exception ex)
            {
                if (debugEnabled) Console.WriteLine("Exception checking array: {0}", ex.Message);
                return false;
            }
        }
        private static async Task<string> ProcessFillPointArray(List<string> splitArgs, string varData, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Processing fill point array with splitArgs: {0}", string.Join(", ", splitArgs));
            // splitArgs[0] = Variable name, I.E. cip, caseAssignment, myCase, etc.
            // splitArgs[1] = Array Functions, I.E. ForEach(), First(), Last()
            // splitArgs[2] = field name if First() or Last()
            // splitArgs[3+] = Function, I.E. SetCasing, GetLabel, MrMrs, etc. if First() or Last()
            var function = splitArgs[1];
            if (function.Contains('('))
            {
                function = function[..function.IndexOf("(", StringComparison.Ordinal)];
                if (debugEnabled) Console.WriteLine("Trimmed function: {0}", function);
            }
            if (string.Equals(function, ArrayFunction.ANY, StringComparison.OrdinalIgnoreCase))
            {
                var result = JsonArrayHasElements.Run(varData, debugEnabled);
                if (debugEnabled) Console.WriteLine("ANY function result: {0}", result);
                return result;
            }
            var processed = await ProcessFirstOrLastArrayFunction(function, varData, splitArgs, debugEnabled);
            if (debugEnabled) Console.WriteLine("ProcessFirstOrLastArrayFunction result: {0}", processed);
            return processed;
        }
        private static async Task<string> ProcessFillPointObject(List<string> splitArgs, string varData, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Processing fill point object with splitArgs: {0}", string.Join(", ", splitArgs));
            //step 1.2.2.1.6
            // splitArgs[0] = Variable name, I.E. cip, caseAssignment, myCase, etc.
            // splitArgs[1] = Field Name, I.E. PersonnelFirstName, Type, etc.
            // splitArgs[2+] = Function, I.E. SetCasing, GetLabel, MrMrs etc.
            // remove variable name from list
            splitArgs.RemoveAt(0);
            if (debugEnabled) Console.WriteLine("Removed variable name, remaining: {0}", string.Join(", ", splitArgs));
            var result = await GetFieldValueAndRunFunctions(splitArgs, varData, debugEnabled);
            if (debugEnabled) Console.WriteLine("GetFieldValueAndRunFunctions result: {0}", result);
            return result;
        }
        private static async Task<string> ProcessFirstOrLastArrayFunction(string function, string varData, List<string> splitArgs, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Processing first/last array function: {0}", function);
            var rowData = string.Empty;
            if (string.Equals(function, ArrayFunction.FIRST, StringComparison.OrdinalIgnoreCase))
            {
                rowData = GetFirstOrLastElement(varData, getFirst: true, debugEnabled);
                if (debugEnabled) Console.WriteLine("Got first element: {0}", rowData);
            }
            else if (string.Equals(function, ArrayFunction.LAST, StringComparison.OrdinalIgnoreCase))
            {
                rowData = GetFirstOrLastElement(varData, getFirst: false, debugEnabled);
                if (debugEnabled) Console.WriteLine("Got last element: {0}", rowData);
            }
            if (splitArgs.Count == 2)
            {
                if (debugEnabled) Console.WriteLine("Only 2 args, returning rowData.");
                return rowData;
            }
            splitArgs.RemoveRange(0, 2);
            if (debugEnabled) Console.WriteLine("Removed first 2 args, remaining: {0}", string.Join(", ", splitArgs));
            var result = await GetFieldValueAndRunFunctions(splitArgs, rowData, debugEnabled);
            if (debugEnabled) Console.WriteLine("GetFieldValueAndRunFunctions result: {0}", result);
            return result;
        }
        private static string GetFirstOrLastElement(string jsonArray, bool getFirst, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Getting {0} element from array: {1}", getFirst ? "first" : "last", jsonArray);
            try
            {
                var doc = JsonDocument.Parse(jsonArray);
                if (doc.RootElement.ValueKind != JsonValueKind.Array)
                {
                    if (debugEnabled) Console.WriteLine("Not an array, returning {{}}.");
                    return "{}";
                }
                var arrayElement = doc.RootElement;
                var length = arrayElement.GetArrayLength();
                if (debugEnabled) Console.WriteLine("Array length: {0}", length);
                if (length == 0)
                {
                    if (debugEnabled) Console.WriteLine("Empty array, returning {{}}.");
                    return "{}";
                }
                var targetElement = getFirst ? arrayElement[0] : arrayElement[length - 1];
                var result = targetElement.GetRawText();
                if (debugEnabled) Console.WriteLine("Target element: {0}", result);
                return result; // Return raw JSON string of the element
            }
            catch (Exception ex)
            {
                if (debugEnabled) Console.WriteLine("Exception getting element: {0}", ex.Message);
                return "{}";
            }
        }
        private static async Task<string> GetFieldValueAndRunFunctions(List<string> remainingArguments, string varData, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Starting GetFieldValueAndRunFunctions with args: {0}, varData: {1}", string.Join(", ", remainingArguments), varData);
            // expected inputs
            // remainingArguments: list of remaining arguments. first value could be field name, or object function
            // remaining data should just be functions
            // varData: the JSON data string containing the field data
            // processedTemplate: the template that is currently processing
            var firstArg = remainingArguments[0];
            if (debugEnabled) Console.WriteLine("First arg: {0}", firstArg);
            var fieldValue = varData;
            // Check if firstArg is a field name (no parentheses)
            if (!firstArg.Contains('('))
            {
                fieldValue = GetFieldValueFromVariable(firstArg, varData, debugEnabled);
                if (debugEnabled) Console.WriteLine("Got field value: {0}", fieldValue);
                remainingArguments.RemoveAt(0);
                if (debugEnabled) Console.WriteLine("Removed field name, remaining: {0}", string.Join(", ", remainingArguments));
            }
            // If firstArg has parentheses, treat it as a function and don't modify fieldValue yet
            foreach (var function in remainingArguments)
            {
                if (debugEnabled) Console.WriteLine("Processing function: {0}", function);
                var parenIndex = function.IndexOf('(');
                var funcName = function[..parenIndex].ToLower();
                var args = ExtractContentInParentheses(function, debugEnabled);
                if (debugEnabled) Console.WriteLine("Func name: {0}, args: {1}", funcName, args);
                switch (funcName)
                {
                    case DocumentTemplateUtil.ChainedFunctions.FORMAT_DATE_FUNC:
                        fieldValue = DocumentGeneratorService.DateFormat.Run(fieldValue, args);
                        if (debugEnabled) Console.WriteLine("After FORMAT_DATE_FUNC: {0}", fieldValue);
                        break;
                    case DocumentTemplateUtil.ObjectFunctions.FORMAT_NAME:
                        fieldValue = FormatName.Run(fieldValue, args);
                        if (debugEnabled) Console.WriteLine("After FORMAT_NAME: {0}", fieldValue);
                        break;
                    case DocumentTemplateUtil.ChainedFunctions.SET_STRING_CASING:
                        fieldValue = SetCasing.Run(fieldValue, args);
                        if (debugEnabled) Console.WriteLine("After SET_STRING_CASING: {0}", fieldValue);
                        break;
                    case DocumentTemplateUtil.ChainedFunctions.IS_NULL_OR_EMPTY:
                        fieldValue = IsNullOrEmpty.Run(fieldValue);
                        if (debugEnabled) Console.WriteLine("After IS_NULL_OR_EMPTY: {0}", fieldValue);
                        break;
                    case DocumentTemplateUtil.ChainedFunctions.FORMAT_NUMBER:
                        fieldValue = FormatNumber.Run(fieldValue, args);
                        if (debugEnabled) Console.WriteLine("After FORMAT_NUMBER: {0}", fieldValue);
                        break;
                    case DocumentTemplateUtil.ChainedFunctions.FORMAT_PHONE_NUMBER:
                        fieldValue = FormatPhoneNumber.Run(fieldValue);
                        if (debugEnabled) Console.WriteLine("After FORMAT_PHONE_NUMBER: {0}", fieldValue);
                        break;
                    default:
                        if (debugEnabled) Console.WriteLine("Unhandled function: {0}", funcName);
                        break;
                }
            }
            if (debugEnabled) Console.WriteLine("Final fieldValue: {0}", fieldValue);
            return fieldValue;
        }
        private static string GetFieldValueFromVariable(string fieldName, string variableValue, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Getting field {0} from: {1}", fieldName, variableValue);
            if (string.IsNullOrWhiteSpace(variableValue))
            {
                if (debugEnabled) Console.WriteLine("VariableValue is empty.");
                return string.Empty;
            }
            try
            {
                var data = JsonSerializer.Deserialize<Dictionary<string, object>>(variableValue);
                if (data == null)
                {
                    if (debugEnabled) Console.WriteLine("Deserialized data is null.");
                    return string.Empty;
                }
                data = ConvertDictToCaseInsensitiveDict(data, debugEnabled);
                if (data.TryGetValue(fieldName, out var value))
                {
                    var result = value?.ToString() ?? string.Empty;
                    if (debugEnabled) Console.WriteLine("Found value: {0}", result);
                    return result;
                }
                if (debugEnabled) Console.WriteLine("Field not found.");
                return string.Empty;
            }
            catch (Exception ex)
            {
                if (debugEnabled) Console.WriteLine("Exception deserializing: {0}", ex.Message);
                return string.Empty;
            }
        }
        private static async Task<string> ResolveFilterString(string entity, string filter, Dictionary<string, string> variableDict, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Starting ResolveFilterString: {0}", filter);
            if (TemplateEntities.EntityHasCaseID(entity))
            {
                string caseIdPlaceholder = $"@[builtin.CaseID]";
                string caseIDArg = $"\"CaseID\":@[builtin.CaseID]";
                if (!filter.Contains(caseIdPlaceholder, StringComparison.OrdinalIgnoreCase))
                {
                    if (string.IsNullOrEmpty(filter))
                    {
                        filter = caseIDArg;
                    }
                    else
                    {
                        filter = caseIDArg + "," + filter;
                    }
                    if (debugEnabled) Console.WriteLine("Added CaseID placeholder. Updated filter: {0}", filter);
                }
            }
            var resolved = filter;
            var matches = Regex.Matches(filter, @"@\[[^\]]+\]");
            foreach (Match match in matches)
            {
                string placeholder = match.Value;
                string value = await GetFillPointValue(placeholder, variableDict, debugEnabled);
                if (debugEnabled) Console.WriteLine("Resolved {0} to {1}", placeholder, value);
                string jsonFormattedValue;
                if (double.TryParse(value, out _))
                {
                    jsonFormattedValue = value; // Number, no quotes
                }
                else if (bool.TryParse(value.ToLowerInvariant(), out _) && (value.ToLowerInvariant() == "true" || value.ToLowerInvariant() == "false"))
                {
                    jsonFormattedValue = value.ToLowerInvariant(); // Bool
                }
                else if (value.ToLowerInvariant() == "null")
                {
                    jsonFormattedValue = "null";
                }
                else
                {
                    jsonFormattedValue = $"\"{value.Replace("\"", "\\\"")}\""; // String, escape quotes
                }
                if (debugEnabled) Console.WriteLine("JSON-formatted: {0}", jsonFormattedValue);
                resolved = resolved.Replace(placeholder, jsonFormattedValue);
            }
            if (debugEnabled) Console.WriteLine("Fully resolved filter: {0}", resolved);
            return resolved;
        }
        private static Dictionary<string, object> ConvertDictToCaseInsensitiveDict(Dictionary<string, object> origDict, bool debugEnabled = false)
        {
            if (debugEnabled) Console.WriteLine("Converting dict to case-insensitive.");
            var newDict = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            foreach (var kvp in origDict)
            {
                newDict[kvp.Key] = kvp.Value;
            }
            if (debugEnabled) Console.WriteLine("Converted dict with {0} entries.", newDict.Count);
            return newDict;
        }
        private static class JsonArrayHasElements
        {
            public static string Run(string data, bool debugEnabled = false)
            {
                if (debugEnabled) Console.WriteLine("Checking if array has elements: {0}", data);
                try
                {
                    // Parse the JSON string into a JsonDocument
                    using var doc = JsonDocument.Parse(data);
                    // Check if the root element is a JSON array
                    if (doc.RootElement.ValueKind != JsonValueKind.Array)
                    {
                        if (debugEnabled) Console.WriteLine("Not an array.");
                        return "false";
                    }
                    if (doc.RootElement.GetArrayLength() == 0)
                    {
                        if (debugEnabled) Console.WriteLine("Empty array.");
                        return "false";
                    }
                }
                catch (Exception ex)
                {
                    if (debugEnabled) Console.WriteLine("Exception parsing: {0}", ex.Message);
                    // Return false if parsing fails (not a valid JSON array)
                    return "false";
                }
                if (debugEnabled) Console.WriteLine("Has elements.");
                return "true";
            }
        }
    }
}