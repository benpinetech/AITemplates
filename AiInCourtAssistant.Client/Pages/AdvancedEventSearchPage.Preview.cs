using Microsoft.AspNetCore.Components.Forms;
using System.Reflection;

namespace AiInCourtAssistant.Client.Pages
{
    public partial class AdvancedEventSearchPage
    {
        // Reflection helpers — rely on centralized TrySet/GetTaskResult/SafeGet* in Shared.cs

        private async Task<string> UploadSessionViaReflectionAsync(IBrowserFile file)
        {
            var api = SessionApi;
            var apiType = api.GetType();

            var uploadAsync = apiType.GetMethods(BindingFlags.Instance | BindingFlags.Public)
                                     .FirstOrDefault(m => m.Name == "UploadAsync" && m.GetParameters().Length == 1);
            if (uploadAsync is not null)
            {
                var dtoType = uploadAsync.GetParameters()[0].ParameterType;
                var dto = Activator.CreateInstance(dtoType)!;
                TrySet(dto, "TenantId", Guid.Empty);
                TrySet(dto, "Courtroom", CurrentCourtroom ?? "Courtroom B");
                TrySet(dto, "DocketDate", CurrentDocketDate ?? DateOnly.FromDateTime(DateTime.Today));
                TrySet(dto, "File", file);

                var task = (Task)uploadAsync.Invoke(api, new[] { dto })!;
                await task.ConfigureAwait(false);
                return GetTaskResult<string>(task) ?? string.Empty;
            }

            var alt = apiType.GetMethods(BindingFlags.Instance | BindingFlags.Public)
                             .FirstOrDefault(m => m.Name.StartsWith("UploadAsync", StringComparison.OrdinalIgnoreCase)
                                               && m.GetParameters().Any(p => typeof(IBrowserFile).IsAssignableFrom(p.ParameterType)));
            if (alt is not null)
            {
                var parms = alt.GetParameters();
                var args = new object?[parms.Length];

                for (int i = 0; i < parms.Length; i++)
                {
                    var p = parms[i];
                    if (typeof(IBrowserFile).IsAssignableFrom(p.ParameterType)) { args[i] = file; continue; }
                    if (p.ParameterType == typeof(string) && p.Name!.Contains("court", StringComparison.OrdinalIgnoreCase)) { args[i] = CurrentCourtroom ?? "Courtroom B"; continue; }
                    if (p.ParameterType == typeof(DateOnly) || p.ParameterType == typeof(DateOnly?)) { args[i] = CurrentDocketDate ?? DateOnly.FromDateTime(DateTime.Today); continue; }
                    if (p.ParameterType == typeof(Guid) || p.ParameterType == typeof(Guid?)) { args[i] = Guid.Empty; continue; }
                    args[i] = p.HasDefaultValue ? p.DefaultValue : null;
                }

                var task = (Task)alt.Invoke(api, args)!;
                await task.ConfigureAwait(false);
                return GetTaskResult<string>(task) ?? string.Empty;
            }

            throw new InvalidOperationException("No compatible upload method found on SessionTranscribeClient.");
        }

        private async Task<IList<object>> GetPreviewViaReflectionAsync(string sessionId)
        {
            var api = SessionApi;
            var apiType = api.GetType();

            var getPrev = apiType.GetMethods(BindingFlags.Instance | BindingFlags.Public)
                                 .FirstOrDefault(m => m.Name.Equals("GetPreviewAsync", StringComparison.OrdinalIgnoreCase)
                                                   || m.Name.Equals("GetPreview", StringComparison.OrdinalIgnoreCase));
            if (getPrev is null) return Array.Empty<object>();

            var parms = getPrev.GetParameters();
            object?[] args = Array.Empty<object?>();

            if (parms.Length == 1)
            {
                // supply sessionId (convert if needed)
                var pt = Nullable.GetUnderlyingType(parms[0].ParameterType) ?? parms[0].ParameterType;
                args = new object?[] { pt == typeof(string) ? sessionId : Convert.ChangeType(sessionId, pt) };
            }
            else if (parms.Length > 1)
            {
                // best effort: fill with defaults, put session first if any string param exists
                args = parms.Select(p =>
                {
                    var t = Nullable.GetUnderlyingType(p.ParameterType) ?? p.ParameterType;
                    if (t == typeof(string) && sessionId is not null) return (object?)sessionId;
                    return p.HasDefaultValue ? p.DefaultValue : null;
                }).ToArray();
            }

            var task = (Task)getPrev.Invoke(api, args)!;
            await task.ConfigureAwait(false);

            var result = GetTaskResult<IEnumerable<object>>(task);
            return (result as IList<object>) ?? result?.ToList() ?? new List<object>();
        }
    }
}
