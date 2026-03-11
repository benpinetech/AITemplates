using DocumentGenerator.Exceptions;
using DocumentGenerator.Models;
using DocumentGenerator.Utils;
using Microsoft.Extensions.DependencyInjection;
using PineCone.BuildingBlocks.Data.Constants;
using PineCone.BuildingBlocks.Data.Interfaces;
using PineCone.BuildingBlocks.Data.Models;
using PineCone.BuildingBlocks.Data.Queries;
using PineCone.BuildingBlocks.Data.Resources;
using PineCone.BuildingBlocks.Data.Results;
using System.Text.Json;

namespace DocumentGenerator;

public class DataProviderService
{
    private readonly IServiceProvider _services;

    public DataProviderService(IServiceProvider services)
    {
        _services = services;
    }

    public async Task<string> GetLabel(int dropdownId, string code, bool useResource)
    {
        var returnStr = code;
        using var scope = _services.CreateScope();
        if (useResource)
        {
            var repository = scope.ServiceProvider
                .GetRequiredService<IDocumentTemplateRepository<SystemDropdownItemResource, SystemDropdownItemQuery>>();


            var query = new SystemDropdownItemQuery()
            {
                SystemDropdownID = new int[] { dropdownId },
                PageSize = 2000
            };
            var result = await repository.GetByQuery(query);

            if (result.Items is null)
            {
                throw new PineTemplateGeneratorException(DocumentGeneratorErrorCodes.NULL_ITEMS_LIST_ON_GET_LABEL,
                    $"DropdownID: {dropdownId}, Code: {code}");
            }
            var dropdownItems = result.Items.ToList();

            var itemMatch = dropdownItems.FirstOrDefault(di => di.Code == code);

            if (itemMatch is not null)
                returnStr = itemMatch.Label;
        }
        else
        {
            var repository = scope.ServiceProvider
                .GetRequiredService<IDocumentTemplateRepository<SystemDropdownItem, SystemDropdownItemQuery>>();


            var query = new SystemDropdownItemQuery()
            {
                SystemDropdownID = new int[] { dropdownId },
                PageSize = 2000
            };

            var result = await repository.GetByQuery(query);

            if (result.Items is null)
            {
                throw new PineTemplateGeneratorException(DocumentGeneratorErrorCodes.NULL_ITEMS_LIST_ON_GET_LABEL,
                    $"DropdownID: {dropdownId}, Code: {code}");
            }
            var dropdownItems = result.Items.ToList();

            var itemMatch = dropdownItems.FirstOrDefault(di => di.Code == code);

            if (itemMatch is not null)
                returnStr = itemMatch.Label;
        }

        return returnStr;
    }

    public async Task<DocumentTemplateWithCollectionsResult> GetTemplateResource(string templateCode)
    {
        using var scope = _services.CreateScope();
        var repository = scope.ServiceProvider.GetRequiredService<IDocumentTemplateActualRepository>();

        var result = await repository.GetWithDetailsByCode(templateCode);

        if (result is null)
        {
            throw new PineTemplateGeneratorException(DocumentGeneratorErrorCodes.NULL_ITEMS_LIST_ON_GET_LABEL,
                $"Template Code: {templateCode}");
        }

        return result;
    }

    public async Task<DocumentTemplateWithCollectionsResult> GetTemplate(int templateId)
    {
        using var scope = _services.CreateScope();
        var repository = scope.ServiceProvider.GetRequiredService<IDocumentTemplateActualRepository>();

        var result = await repository.GetWithDetails(templateId);

        if (result is null)
        {
            throw new PineTemplateGeneratorException(DocumentGeneratorErrorCodes.NULL_ITEMS_LIST_ON_GET_LABEL,
                $"TemplateID: {templateId}");
        }

        return result;
    }

    public async Task<string> GetDocumentVariableValue(GetDataCommand command, bool useResource = true)
    {
        string cmdArg;
        if (string.Equals(command.Method, "GetByID", StringComparison.OrdinalIgnoreCase))
        {
            cmdArg = $"{command.RootID}";
        }
        else
        {
            var filterDict = new Dictionary<string, object>();
            foreach (var filter in command.Filters)
            {
                var key = filter.Key.Trim().Trim('"');
                var valueStr = filter.Value.Trim().Trim('"');
                object valObj;

                // Check if the field is an array type based on entity and key
                if (IsArrayField(command.Entity, key))
                {
                    // If the value is not already wrapped in [], wrap it
                    if (!valueStr.StartsWith("[") || !valueStr.EndsWith("]"))
                    {
                        valueStr = $"[{valueStr}]";
                    }
                }

                if (valueStr.StartsWith("[") && valueStr.EndsWith("]"))
                {
                    var arrayStr = valueStr.Substring(1, valueStr.Length - 2);
                    var parts = arrayStr.Split(',', StringSplitOptions.RemoveEmptyEntries);
                    var list = new List<object>();
                    foreach (var part in parts)
                    {
                        list.Add(ParseSimpleValue(part.Trim()));
                    }
                    valObj = list;
                }
                else
                {
                    valObj = ParseSimpleValue(valueStr);
                }
                filterDict.Add(key, valObj);
            }
            cmdArg = JsonSerializer.Serialize(filterDict);
        }
        //remove any carraige returns or new lines in the query portion so we don't encounter parsing issues in GetValue.
        cmdArg = cmdArg.Replace("\r", "").Replace("\n", "");
        try
        {
            var value = await GetValue(command.Entity, command.Method, cmdArg, useResource);
            return value;
        }
        catch (Exception ex)
        {
            //If we have an error in the query or get Value, then return an empty array so we can proceed without error
            return "[]";
        }
    }

    private object ParseSimpleValue(string s)
    {
        s = s.Trim();
        bool wasQuoted = s.StartsWith("\"") && s.EndsWith("\"");
        if (wasQuoted)
        {
            s = s.Substring(1, s.Length - 2);
        }
        if (!wasQuoted)
        {
            if (int.TryParse(s, out int i)) return i;
            if (double.TryParse(s, out double d)) return d;
            if (bool.TryParse(s, out bool b)) return b;
        }
        return s; // treat as string
    }


    public async Task<string> GetDocumentVariableValue(string fullCommand
        , Dictionary<string, string> variables, bool useResource = true)
    {
        var commandParts = DocumentTemplateUtil.GetCommandParts(fullCommand);

        if (commandParts.Length < 2)
            throw new PineTemplateGeneratorException(DocumentGeneratorErrorCodes.ENTITY_COMMAND_NOT_VALID, fullCommand);

        // get entity arg
        var entity = commandParts[0];
        entity = entity.Trim()[1..];
        // get command arg, break into parts
        var command = commandParts[1];
        var firstParenIndex = command.IndexOf('(');
        var commandFunc = command[..firstParenIndex];
        var commandArg = DocumentTemplateUtil.ExtractTextBetweenParentheses(command);
        // get field arg
        var field = string.Empty;
        if (commandParts.Length > 2)
            field = commandParts[2];
        // get any additional function calls
        if (commandParts.Length > 3)
        {
            throw new PineTemplateGeneratorException(DocumentGeneratorErrorCodes.FUNCTIONS_NOT_AVAILABLE_ON_GET_DATA, fullCommand);
        }

        // replace any variables with their value in the command arg
        foreach (var docVar in variables)
        {
            commandArg = commandArg.Replace(docVar.Key, docVar.Value, StringComparison.OrdinalIgnoreCase);
        }

        if (string.Equals(commandFunc, "GetByQuery", StringComparison.OrdinalIgnoreCase))
        {
            commandArg = "{" + commandArg + "}";
        }

        var value = await GetValue(entity, commandFunc, commandArg, useResource);

        if (!string.IsNullOrEmpty(field))
        {
            var fieldVal = CleanRawText(JsonDocument.Parse(value).RootElement.GetProperty(field).GetRawText());
            if (string.IsNullOrEmpty(fieldVal))
                throw new PineTemplateGeneratorException(DocumentGeneratorErrorCodes.DATA_PROVIDER_FIELD_NOT_FOUND, field);

            value = fieldVal;

        }

        return value;
    }

    private async Task<string> GetValue(string entity, string commandFunc, string commandArg, bool useResource = true)
    {
        // case entity switch
        switch (entity)
        {
            case TemplateEntities.Case:
                if (useResource) return await GetData<CaseResource, CaseQuery>(commandFunc, commandArg);
                return await GetData<Case, CaseQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseAgency:
                if (useResource) return await GetData<CaseAgencyResource, CaseAgencyQuery>(commandFunc, commandArg);
                return await GetData<CaseAgency, CaseAgencyQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseArrestInfo:
                if (useResource) return await GetData<CaseArrestInfoResource, CaseArrestInfoQuery>(commandFunc, commandArg);
                return await GetData<CaseArrestInfo, CaseArrestInfoQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseAssignment:
                if (useResource) return await GetData<CaseAssignmentResource, CaseAssignmentQuery>(commandFunc, commandArg);
                return await GetData<CaseAssignment, CaseAssignmentQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseBond:
                if (useResource) return await GetData<CaseBondResource, CaseBondQuery>(commandFunc, commandArg);
                return await GetData<CaseBond, CaseBondQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseBondCondition:
                if (useResource) return await GetData<CaseBondConditionResource, CaseBondConditionQuery>(commandFunc, commandArg);
                return await GetData<CaseBondCondition, CaseBondConditionQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseCategory:
                if (useResource) return await GetData<CaseCategoryResource, CaseCategoryQuery>(commandFunc, commandArg);
                return await GetData<CaseCategory, CaseCategoryQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseCharge:
                if (useResource) return await GetData<CaseChargeResource, CaseChargeQuery>(commandFunc, commandArg);
                return await GetData<CaseCharge, CaseChargeQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseChargeElement:
                if (useResource) return await GetData<CaseChargeElementResource, CaseChargeElementQuery>(commandFunc, commandArg);
                return await GetData<CaseChargeElement, CaseChargeElementQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseChargeExhibit:
                if (useResource) return await GetData<CaseChargeExhibitResource, CaseChargeExhibitQuery>(commandFunc, commandArg);
                return await GetData<CaseChargeExhibit, CaseChargeExhibitQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseChargeHistory:
                if (useResource) return await GetData<CaseChargeHistoryResource, CaseChargeHistoryQuery>(commandFunc, commandArg);
                return await GetData<CaseChargeHistory, CaseChargeHistoryQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseChargeIntoxicant:
                if (useResource) return await GetData<CaseChargeIntoxicantResource, CaseChargeIntoxicantQuery>(commandFunc, commandArg);
                return await GetData<CaseChargeIntoxicant, CaseChargeIntoxicantQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseChargeInvolvement:
                if (useResource) return await GetData<CaseChargeInvolvementResource, CaseChargeInvolvementQuery>(commandFunc, commandArg);
                return await GetData<CaseChargeInvolvement, CaseChargeInvolvementQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseChargeLanguage:
                if (useResource) return await GetData<CaseChargeLanguageResource, CaseChargeLanguageQuery>(commandFunc, commandArg);
                return await GetData<CaseChargeLanguage, CaseChargeLanguageQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseCorrespondence:
                if (useResource) return await GetData<CaseCorrespondenceResource, CaseCorrespondenceQuery>(commandFunc, commandArg);
                return await GetData<CaseCorrespondence, CaseCorrespondenceQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseCorrespondenceAssignment:
                if (useResource) return await GetData<CaseCorrespondenceAssignmentResource, CaseCorrespondenceAssignmentQuery>(commandFunc, commandArg);
                return await GetData<CaseCorrespondenceAssignment, CaseCorrespondenceAssignmentQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseCorrespondenceInvolvement:
                if (useResource) return await GetData<CaseCorrespondenceInvolvementResource, CaseCorrespondenceInvolvementQuery>(commandFunc, commandArg);
                return await GetData<CaseCorrespondenceInvolvement, CaseCorrespondenceInvolvementQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseCorrespondenceMediaItem:
                if (useResource) return await GetData<CaseCorrespondenceMediaItemResource, CaseCorrespondenceMediaItemQuery>(commandFunc, commandArg);
                return await GetData<CaseCorrespondenceMediaItem, CaseCorrespondenceMediaItemQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseCorrespondenceResult:
                if (useResource) return await GetData<CaseCorrespondenceResultResource, CaseCorrespondenceResultQuery>(commandFunc, commandArg);
                return await GetData<CaseCorrespondenceResult, CaseCorrespondenceResultQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseDisposition:
                if (useResource) return await GetData<CaseDispositionResource, CaseDispositionQuery>(commandFunc, commandArg);
                return await GetData<CaseDisposition, CaseDispositionQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseDrugTest:
                if (useResource) return await GetData<CaseDrugTestResource, CaseDrugTestQuery>(commandFunc, commandArg);
                return await GetData<CaseDrugTest, CaseDrugTestQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseDrugTestResult:
                if (useResource) return await GetData<CaseDrugTestResultResource, CaseDrugTestResultQuery>(commandFunc, commandArg);
                return await GetData<CaseDrugTestResult, CaseDrugTestResultQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseElement:
                if (useResource) return await GetData<CaseElementResource, CaseElementQuery>(commandFunc, commandArg);
                return await GetData<CaseElement, CaseElementQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseExhibit:
                if (useResource) return await GetData<CaseExhibitResource, CaseExhibitQuery>(commandFunc, commandArg);
                return await GetData<CaseExhibit, CaseExhibitQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseFee:
                if (useResource) return await GetData<CaseFeeResource, CaseFeeQuery>(commandFunc, commandArg);
                return await GetData<CaseFee, CaseFeeQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseFlag:
                if (useResource) return await GetData<CaseFlagResource, CaseFlagQuery>(commandFunc, commandArg);
                return await GetData<CaseFlag, CaseFlagQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseInvolvement:
                if (useResource) return await GetData<CaseInvolvementResource, CaseInvolvementQuery>(commandFunc, commandArg);
                return await GetData<CaseInvolvement, CaseInvolvementQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseMediaFolder:
                if (useResource) return await GetData<CaseMediaFolderResource, CaseMediaFolderQuery>(commandFunc, commandArg);
                return await GetData<CaseMediaFolder, CaseMediaFolderQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseNameTemplate:
                if (useResource) return await GetData<CaseNameTemplateResource, CaseNameTemplateQuery>(commandFunc, commandArg);
                return await GetData<CaseNameTemplate, CaseNameTemplateQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseNote:
                if (useResource) return await GetData<CaseNoteResource, CaseNoteQuery>(commandFunc, commandArg);
                return await GetData<CaseNote, CaseNoteQuery>(commandFunc, commandArg);
            case TemplateEntities.CasePlea:
                if (useResource) return await GetData<CasePleaResource, CasePleaQuery>(commandFunc, commandArg);
                return await GetData<CasePlea, CasePleaQuery>(commandFunc, commandArg);
            case TemplateEntities.CasePleaAgreement:
                if (useResource) return await GetData<CasePleaAgreementResource, CasePleaAgreementQuery>(commandFunc, commandArg);
                return await GetData<CasePleaAgreement, CasePleaAgreementQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseRelationship:
                if (useResource) return await GetData<CaseRelationshipResource, CaseRelationshipQuery>(commandFunc, commandArg);
                return await GetData<CaseRelationship, CaseRelationshipQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseSentence:
                if (useResource) return await GetData<CaseSentenceResource, CaseSentenceQuery>(commandFunc, commandArg);
                return await GetData<CaseSentence, CaseSentenceQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseSentenceCondition:
                if (useResource) return await GetData<CaseSentenceConditionResource, CaseSentenceConditionQuery>(commandFunc, commandArg);
                return await GetData<CaseSentenceCondition, CaseSentenceConditionQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseSentenceTimeTracking:
                if (useResource) return await GetData<CaseSentenceTimeTrackingResource, CaseSentenceTimeTrackingQuery>(commandFunc, commandArg);
                return await GetData<CaseSentenceTimeTracking, CaseSentenceTimeTrackingQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseStatusHistory:
                if (useResource) return await GetData<CaseStatusHistoryResource, CaseStatusHistoryQuery>(commandFunc, commandArg);
                return await GetData<CaseStatusHistory, CaseStatusHistoryQuery>(commandFunc, commandArg);
            case TemplateEntities.CaseTimeTracking:
                if (useResource) return await GetData<CaseTimeTrackingResource, CaseTimeTrackingQuery>(commandFunc, commandArg);
                return await GetData<CaseTimeTracking, CaseTimeTrackingQuery>(commandFunc, commandArg);
        }
        // event entity switch
        switch (entity)
        {
            case TemplateEntities.Event:
                if (useResource) return await GetData<EventResource, EventQuery>(commandFunc, commandArg);
                return await GetData<Event, EventQuery>(commandFunc, commandArg);
            case TemplateEntities.EventAssignment:
                if (useResource) return await GetData<EventAssignmentResource, EventAssignmentQuery>(commandFunc, commandArg);
                return await GetData<EventAssignment, EventAssignmentQuery>(commandFunc, commandArg);
            case TemplateEntities.EventInvolvement:
                if (useResource) return await GetData<EventInvolvementResource, EventInvolvementQuery>(commandFunc, commandArg);
                return await GetData<EventInvolvement, EventInvolvementQuery>(commandFunc, commandArg);
            case TemplateEntities.EventMediaItem:
                if (useResource) return await GetData<EventMediaItemResource, EventMediaItemQuery>(commandFunc, commandArg);
                return await GetData<EventMediaItem, EventMediaItemQuery>(commandFunc, commandArg);
            case TemplateEntities.EventResult:
                if (useResource) return await GetData<EventResultResource, EventResultQuery>(commandFunc, commandArg);
                return await GetData<EventResult, EventResultQuery>(commandFunc, commandArg);
            case TemplateEntities.Task:
                if (useResource) return await GetData<TaskResource, TaskQuery>(commandFunc, commandArg);
                return await GetData<TaskModel, TaskQuery>(commandFunc, commandArg);
            case TemplateEntities.TaskAssignment:
                if (useResource) return await GetData<TaskAssignmentResource, TaskAssignmentQuery>(commandFunc, commandArg);
                return await GetData<TaskAssignment, TaskAssignmentQuery>(commandFunc, commandArg);
            case TemplateEntities.TaskMediaItem:
                if (useResource) return await GetData<TaskMediaItemResource, TaskMediaItemQuery>(commandFunc, commandArg);
                return await GetData<TaskMediaItem, TaskMediaItemQuery>(commandFunc, commandArg);
            case TemplateEntities.TaskResult:
                if (useResource) return await GetData<TaskResultResource, TaskResultQuery>(commandFunc, commandArg);
                return await GetData<TaskResult, TaskResultQuery>(commandFunc, commandArg);
        }
        // name entity switch
        switch (entity)
        {
            case TemplateEntities.Name:
                if (useResource) return await GetData<NameResource, NameQuery>(commandFunc, commandArg);
                return await GetData<Name, NameQuery>(commandFunc, commandArg);
            case TemplateEntities.NameAddress:
                if (useResource) return await GetData<NameAddressResource, NameAddressQuery>(commandFunc, commandArg);
                return await GetData<NameAddress, NameAddressQuery>(commandFunc, commandArg);
            case TemplateEntities.NameCorrespondence:
                if (useResource) return await GetData<NameCorrespondenceResource, NameCorrespondenceQuery>(commandFunc, commandArg);
                return await GetData<NameCorrespondence, NameCorrespondenceQuery>(commandFunc, commandArg);
            case TemplateEntities.NameCorrespondenceAssignment:
                if (useResource) return await GetData<NameCorrespondenceAssignmentResource, NameCorrespondenceAssignmentQuery>(commandFunc, commandArg);
                return await GetData<NameCorrespondenceAssignment, NameCorrespondenceAssignmentQuery>(commandFunc, commandArg);
            case TemplateEntities.NameCorrespondenceInvolvement:
                if (useResource) return await GetData<NameCorrespondenceInvolvementResource, NameCorrespondenceInvolvementQuery>(commandFunc, commandArg);
                return await GetData<NameCorrespondenceInvolvement, NameCorrespondenceInvolvementQuery>(commandFunc, commandArg);
            case TemplateEntities.NameCorrespondenceMediaItem:
                if (useResource) return await GetData<NameCorrespondenceMediaItemResource, NameCorrespondenceMediaItemQuery>(commandFunc, commandArg);
                return await GetData<NameCorrespondenceMediaItem, NameCorrespondenceMediaItemQuery>(commandFunc, commandArg);
            case TemplateEntities.NameCorrespondenceResult:
                if (useResource) return await GetData<NameCorrespondenceResultResource, NameCorrespondenceResultQuery>(commandFunc, commandArg);
                return await GetData<NameCorrespondenceResult, NameCorrespondenceResultQuery>(commandFunc, commandArg);
            case TemplateEntities.NameElement:
                if (useResource) return await GetData<NameElementResource, NameElementQuery>(commandFunc, commandArg);
                return await GetData<NameElement, NameElementQuery>(commandFunc, commandArg);
            case TemplateEntities.NameEmail:
                if (useResource) return await GetData<NameEmailResource, NameEmailQuery>(commandFunc, commandArg);
                return await GetData<NameEmail, NameEmailQuery>(commandFunc, commandArg);
            case TemplateEntities.NameFlag:
                if (useResource) return await GetData<NameFlagResource, NameFlagQuery>(commandFunc, commandArg);
                return await GetData<NameFlag, NameFlagQuery>(commandFunc, commandArg);
            case TemplateEntities.NameImage:
                if (useResource) return await GetData<NameImageResource, NameImageQuery>(commandFunc, commandArg);
                return await GetData<NameImage, NameImageQuery>(commandFunc, commandArg);
            case TemplateEntities.NameMediaFolder:
                if (useResource) return await GetData<NameMediaFolderResource, NameMediaFolderQuery>(commandFunc, commandArg);
                return await GetData<NameMediaFolder, NameMediaFolderQuery>(commandFunc, commandArg);
            case TemplateEntities.NameNote:
                if (useResource) return await GetData<NameNoteResource, NameNoteQuery>(commandFunc, commandArg);
                return await GetData<NameNote, NameNoteQuery>(commandFunc, commandArg);
            case TemplateEntities.NameNumber:
                if (useResource) return await GetData<NameNumberResource, NameNumberQuery>(commandFunc, commandArg);
                return await GetData<NameNumber, NameNumberQuery>(commandFunc, commandArg);
            case TemplateEntities.NamePhone:
                if (useResource) return await GetData<NamePhoneResource, NamePhoneQuery>(commandFunc, commandArg);
                return await GetData<NamePhone, NamePhoneQuery>(commandFunc, commandArg);
            case TemplateEntities.NameRelationship:
                if (useResource) return await GetData<NameRelationshipResource, NameRelationshipQuery>(commandFunc, commandArg);
                return await GetData<NameRelationship, NameRelationshipQuery>(commandFunc, commandArg);
        }
        // personnel entity switch
        switch (entity)
        {
            case TemplateEntities.Personnel:
                if (useResource) return await GetData<PersonnelResource, PersonnelQuery>(commandFunc, commandArg);
                return await GetData<Personnel, PersonnelQuery>(commandFunc, commandArg);
            case TemplateEntities.PersonnelAddress:
                if (useResource) return await GetData<PersonnelAddressResource, PersonnelAddressQuery>(commandFunc, commandArg);
                return await GetData<PersonnelAddress, PersonnelAddressQuery>(commandFunc, commandArg);
            case TemplateEntities.PersonnelAgency:
                if (useResource) return await GetData<PersonnelAgencyResource, PersonnelAgencyQuery>(commandFunc, commandArg);
                return await GetData<PersonnelAgency, PersonnelAgencyQuery>(commandFunc, commandArg);
            case TemplateEntities.PersonnelElement:
                if (useResource) return await GetData<PersonnelElementResource, PersonnelElementQuery>(commandFunc, commandArg);
                return await GetData<PersonnelElement, PersonnelElementQuery>(commandFunc, commandArg);
            case TemplateEntities.PersonnelEmail:
                if (useResource) return await GetData<PersonnelEmailResource, PersonnelEmailQuery>(commandFunc, commandArg);
                return await GetData<PersonnelEmail, PersonnelEmailQuery>(commandFunc, commandArg);
            case TemplateEntities.PersonnelFlag:
                if (useResource) return await GetData<PersonnelFlagResource, PersonnelFlagQuery>(commandFunc, commandArg);
                return await GetData<PersonnelFlag, PersonnelFlagQuery>(commandFunc, commandArg);
            case TemplateEntities.PersonnelImage:
                if (useResource) return await GetData<PersonnelImageResource, PersonnelImageQuery>(commandFunc, commandArg);
                return await GetData<PersonnelImage, PersonnelImageQuery>(commandFunc, commandArg);
            case TemplateEntities.PersonnelMediaFolder:
                if (useResource) return await GetData<PersonnelMediaFolderResource, PersonnelMediaFolderQuery>(commandFunc, commandArg);
                return await GetData<PersonnelMediaFolder, PersonnelMediaFolderQuery>(commandFunc, commandArg);
            case TemplateEntities.PersonnelNumber:
                if (useResource) return await GetData<PersonnelNumberResource, PersonnelNumberQuery>(commandFunc, commandArg);
                return await GetData<PersonnelNumber, PersonnelNumberQuery>(commandFunc, commandArg);
            case TemplateEntities.PersonnelPhone:
                if (useResource) return await GetData<PersonnelPhoneResource, PersonnelPhoneQuery>(commandFunc, commandArg);
                return await GetData<PersonnelPhone, PersonnelPhoneQuery>(commandFunc, commandArg);
            case TemplateEntities.PersonnelTeam:
                if (useResource) return await GetData<PersonnelTeamResource, PersonnelTeamQuery>(commandFunc, commandArg);
                return await GetData<PersonnelTeam, PersonnelTeamQuery>(commandFunc, commandArg);
        }
        // system entity switch
        switch (entity)
        {
            case TemplateEntities.SystemUser:
                if (useResource) return await GetData<SystemUserResource, SystemUserQuery>(commandFunc, commandArg);
                return await GetData<SystemUser, SystemUserQuery>(commandFunc, commandArg);
            case TemplateEntities.SecurityLog:
                if (useResource) return await GetData<SecurityLogResource, SecurityLogQuery>(commandFunc, commandArg);
                return await GetData<SecurityLog, SecurityLogQuery>(commandFunc, commandArg);
            case TemplateEntities.SystemAgency:
                if (useResource) return await GetData<SystemAgencyResource, SystemAgencyQuery>(commandFunc, commandArg);
                return await GetData<SystemAgency, SystemAgencyQuery>(commandFunc, commandArg);
            case TemplateEntities.SystemAgencyAddress:
                if (useResource) return await GetData<SystemAgencyAddressResource, SystemAgencyAddressQuery>(commandFunc, commandArg);
                return await GetData<SystemAgencyAddress, SystemAgencyAddressQuery>(commandFunc, commandArg);
            case TemplateEntities.SystemAgencyEmail:
                if (useResource) return await GetData<SystemAgencyEmailResource, SystemAgencyEmailQuery>(commandFunc, commandArg);
                return await GetData<SystemAgencyEmail, SystemAgencyEmailQuery>(commandFunc, commandArg);
            case TemplateEntities.SystemAgencyPhone:
                if (useResource) return await GetData<SystemAgencyPhoneResource, SystemAgencyPhoneQuery>(commandFunc, commandArg);
                return await GetData<SystemAgencyPhone, SystemAgencyPhoneQuery>(commandFunc, commandArg);
            case TemplateEntities.SystemStatute:
                if (useResource) return await GetData<SystemStatuteResource, SystemStatuteQuery>(commandFunc, commandArg);
                return await GetData<SystemStatute, SystemStatuteQuery>(commandFunc, commandArg);
            case TemplateEntities.SystemStatuteChargingLanguage:
                if (useResource) return await GetData<SystemStatuteChargingLanguageResource, SystemStatuteChargingLanguageQuery>(commandFunc, commandArg);
                return await GetData<SystemStatuteChargingLanguage, SystemStatuteChargingLanguageQuery>(commandFunc, commandArg);
            case TemplateEntities.SystemStatuteElement:
                if (useResource) return await GetData<SystemStatuteElementResource, SystemStatuteElementQuery>(commandFunc, commandArg);
                return await GetData<SystemStatuteElement, SystemStatuteElementQuery>(commandFunc, commandArg);
            case TemplateEntities.SystemStatuteText:
                if (useResource) return await GetData<SystemStatuteTextResource, SystemStatuteTextQuery>(commandFunc, commandArg);
                return await GetData<SystemStatuteText, SystemStatuteTextQuery>(commandFunc, commandArg);
            case TemplateEntities.Payment:
                if (useResource) return await GetData<PaymentResource, PaymentQuery>(commandFunc, commandArg);
                return await GetData<Payment, PaymentQuery>(commandFunc, commandArg);
            case TemplateEntities.PaymentObligationAllocation:
                if (useResource) return await GetData<PaymentObligationAllocationResource, PaymentObligationAllocationQuery>(commandFunc, commandArg);
                return await GetData<PaymentObligationAllocation, PaymentObligationAllocationQuery>(commandFunc, commandArg);
            case TemplateEntities.SystemSetting:
                if (useResource) return await GetData<SystemSettingResource, SystemSettingQuery>(commandFunc, commandArg);
                return await GetData<SystemSetting, SystemSettingQuery>(commandFunc, commandArg);
            case TemplateEntities.SystemDropdownItem:
                if (useResource) return await GetData<SystemDropdownItemResource, SystemDropdownItemQuery>(commandFunc, commandArg);
                return await GetData<SystemDropdownItem, SystemDropdownItemQuery>(commandFunc, commandArg);

        }

        throw new PineTemplateGeneratorException(DocumentGeneratorErrorCodes.INVALID_ENTITY_NAME, entity);
    }

    private async Task<string> GetData<TResource, TQuery>(string command, string data) where TQuery : new()
    {
        switch (command)
        {
            case "GetByID":
                return await GetByID<TResource, TQuery>(data);
            case "GetByQuery":
                return await GetByQuery<TResource, TQuery>(data);
        }

        return string.Empty;
    }

    private async Task<string> GetByID<TResource, TQuery>(string id)
    {
        if (int.TryParse(id, out var resourceId) == false)
            throw new PineTemplateGeneratorException(DocumentGeneratorErrorCodes.INVALID_ID_STRING, id);

        using var scope = _services.CreateScope();
        var repository = scope.ServiceProvider.GetRequiredService<IDocumentTemplateRepository<TResource, TQuery>>();
        var obj = await repository.GetByID(resourceId);
        return JsonSerializer.Serialize(obj);
    }

    private async Task<string> GetByQuery<TResource, TQuery>(string query) where TQuery : new()
    {
        using var scope = _services.CreateScope();
        var repository = scope.ServiceProvider.GetRequiredService<IDocumentTemplateRepository<TResource, TQuery>>();
        var options = new JsonSerializerOptions
        {
            PropertyNameCaseInsensitive = true
        };

        int page = 1;
        int totalRecords = 0;
        var deserializedQuery = JsonSerializer.Deserialize<TQuery>(query, options) ?? new TQuery();
        var pageProp = typeof(TQuery).GetProperty("Page");
        pageProp!.SetValue(deserializedQuery, page);
        var pageSizeProp = typeof(TQuery).GetProperty("PageSize");
        pageSizeProp!.SetValue(deserializedQuery, 250);

        var data = await repository.GetByQuery(deserializedQuery);
        totalRecords += data.RecordsReturned;

        if (data.RecordsInQuery > 10000)
            throw new Exception("Cannot retreive data for template, row count exceeds 10000");

        while (totalRecords < data.RecordsInQuery)
        {
            page++;
            pageProp!.SetValue(deserializedQuery, page);
            var nextPage = await repository.GetByQuery(deserializedQuery);

            if (nextPage.RecordsReturned > 0)
                data.Items!.AddRange(nextPage.Items!);

            totalRecords += nextPage.RecordsReturned;

            // temporary bug fix in 5.2.0 to cap records returned to 1000
            if (page > 4)
                break;
        }
        if (data.Items != null && data.Items.Count > 0)
            return JsonSerializer.Serialize(data.Items);
        else
            return "[]";
    }

    private static string CleanRawText(string rawText)
    {
        if (rawText.StartsWith('"'))
            rawText = rawText.Substring(1, rawText.Length - 1);

        if (rawText.EndsWith('"'))
            rawText = rawText.Substring(0, rawText.Length - 1);

        return rawText;
    }

    private bool IsArrayField(string entity, string field)
    {
        // Check if the entity exists and if the field is in its array fields list
        if (EntityFieldArrays.ArrayFieldsByEntity.TryGetValue(entity, out var arrayFields))
        {
            return arrayFields.Contains(field, StringComparer.OrdinalIgnoreCase);
        }

        return false;
    }


}
